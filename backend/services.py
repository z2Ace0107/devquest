# -*- coding: utf-8 -*-
"""DevQuest — 业务逻辑层

将 MCP Server 的 tool handler 与数据操作解耦。
所有数据库写入、分类、评分、索引操作集中在此。
"""

import json
import logging
from datetime import datetime, timezone
from typing import Optional

from backend.database import SessionLocal
from backend.models import Problem, Project
from backend import classifier, scorer, vector_search

logger = logging.getLogger(__name__)


def _get_or_create_project(db, project_name: str) -> Project:
    proj = db.query(Project).filter_by(name=project_name).first()
    if not proj:
        proj = Project(name=project_name)
        db.add(proj)
        db.flush()
    return proj


def save_problem_service(
    error: str,
    solution: str,
    attempts: Optional[list] = None,
    environment: Optional[dict] = None,
    project: Optional[str] = None,
    problem_type: Optional[str] = None,
    tech_stack: Optional[list] = None,
) -> dict:
    """结构化录入一个问题，跳过 LLM 提取步骤。

    参数:
        error: 错误描述或复现步骤
        solution: 最终解决方案
        attempts: 尝试过的方案列表
        environment: 运行环境 dict \xe5\xa6\x82 {"os":"win11","python":"3.12"}
        project: 项目名，默认 "Unknown"
        problem_type: 问题类型，不传则自动分类
        tech_stack: 技术栈列表，不传则自动分类
    """
    project_name = project or "Unknown"
    attempts = attempts or []
    tech_stack_str = ",".join(tech_stack) if tech_stack else None
    env_json = json.dumps(environment, ensure_ascii=False) if environment else None

    # 自动分类（如果未传入）
    if not problem_type or not tech_stack_str:
        try:
            auto_ts, auto_pt = classifier.classify_problem(
                title=error[:100],
                description=error,
                solution=solution,
            )
            if not tech_stack_str and auto_ts:
                tech_stack_str = auto_ts
            if not problem_type and auto_pt:
                problem_type = auto_pt
        except Exception:
            logger.exception("自动分类失败")

    # 评分
    priority_score = 5
    try:
        scores = scorer.score_problem(
            title=error[:100],
            description=error,
            attempts=json.dumps(attempts, ensure_ascii=False),
            solution=solution,
        )
        priority_score = scores.get("total", 5)
    except Exception:
        logger.exception("评分失败")

    db = SessionLocal()
    try:
        proj = _get_or_create_project(db, project_name)

        # 语义去重
        merged_with = None
        try:
            similar_pid, distance = vector_search.search_similar(
                title=error[:100],
                description=error,
            )
            if similar_pid and distance < 0.125:
                existing = db.query(Problem).filter_by(id=similar_pid).first()
                if existing:
                    from backend.extractor import _merge_problem
                    _merge_problem(existing, {
                        "attempts": attempts,
                        "solution": solution,
                        "tech_stack": tech_stack_str or "",
                    }, raw_conversation=error)
                    existing.environment = env_json
                    db.commit()
                    vector_search.add_to_index(similar_pid)
                    return {
                        "problem_id": similar_pid,
                        "merged": True,
                        "merged_with": similar_pid,
                        "solution_version": existing.solution_version,
                    }
        except Exception:
            logger.exception("去重检查失败")

        # 新建
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        problem = Problem(
            project_id=proj.id,
            title=error[:200] if len(error) > 200 else error,
            description=error,
            attempts=json.dumps(attempts, ensure_ascii=False) if attempts else "[]",
            solution=solution,
            tech_stack=tech_stack_str or "",
            problem_type=problem_type or "",
            raw_conversation=error,
            priority_score=priority_score,
            environment=env_json,
            first_seen_at=now,
            solution_version=1,
        )
        db.add(problem)
        db.flush()
        pid = problem.id
        db.commit()

        # 索引
        vector_search.add_to_index(pid)

        return {
            "problem_id": pid,
            "merged": False,
            "merged_with": None,
            "solution_version": 1,
        }
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def record_feedback_service(problem_id: int, helpful: bool, note: Optional[str] = None) -> dict:
    """记录用户对某条经验的反馈。

    helpful=True  → feedback_score 趋向 1.0，usage_count +10
    helpful=False → feedback_score 趋向 0.0，usage_count -2 (不低于 0)
    """
    db = SessionLocal()
    try:
        problem = db.query(Problem).filter_by(id=problem_id).first()
        if not problem:
            return {"error": f"问题 #{problem_id} 不存在"}

        old_score = problem.feedback_score or 0.0
        old_count = problem.feedback_count or 0
        old_usage = problem.usage_count or 0

        # 增量更新 feedback_score
        vote = 1.0 if helpful else 0.0
        new_score = (old_score * old_count + vote) / (old_count + 1)

        problem.feedback_score = round(new_score, 4)
        problem.feedback_count = old_count + 1

        # 更新 usage_count
        if helpful:
            problem.usage_count = old_usage + 10
        else:
            problem.usage_count = max(0, old_usage - 2)

        db.commit()

        return {
            "problem_id": problem_id,
            "helpful": helpful,
            "feedback_score": problem.feedback_score,
            "feedback_count": problem.feedback_count,
            "usage_count": problem.usage_count,
        }
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
