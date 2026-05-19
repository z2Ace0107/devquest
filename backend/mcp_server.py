# -*- coding: utf-8 -*-
"""
DevQuest — MCP Server

基于 Python MCP SDK，将经验库能力以标准 MCP 协议暴露给 MCP Client。
替代原有 FastAPI + Streamlit 架构。

启动方式:
  python backend/mcp_server.py          # stdio 模式 (MCP Client)
  python backend/mcp_server.py --http   # HTTP 模式 (开发调试)
"""

import logging
import os
import sys
from pathlib import Path
from typing import Optional

# 确保项目根目录在 path 中
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from mcp.server.fastmcp import FastMCP

from backend.database import init_db, SessionLocal
from backend.models import Problem, Project
from backend import extractor, classifier, scorer, star_gen, vector_search, session_ingestor, rule_maker
from backend import services

# ── 启动初始化 ──────────────────────────────────────────────────
init_db()

# 确保 ChromaDB 持久化目录存在
chroma_dir = Path(__file__).resolve().parent.parent / "data" / "chroma_db"
chroma_dir.mkdir(parents=True, exist_ok=True)

# ── MCP Server ──────────────────────────────────────────────────
mcp = FastMCP(
    "DevQuest",
    instructions="开发者外脑 Agent — 自动从 Claude 对话中沉淀技术经验，双通道混合检索历史方案。提供语义搜索、session 摄入、问题管理、统计概览、STAR 故事生成等功能。",
)


# ══════════════════════════════════════════════════════════════════
# 内部辅助函数
# ══════════════════════════════════════════════════════════════════

def _run_extract_pipeline(conversation_text: str, project_name: str) -> dict:
    """
    完整提取流水线：extractor → classifier → scorer → 双通道索引。
    每步独立 try/except，失败不影响后续步骤。
    """
    # Step 1: 提取问题（必须成功）
    problems = extractor.extract_problems(
        conversation_text=conversation_text,
        project_name=project_name,
    )
    if not problems:
        return {"count": 0, "problems": [], "indexed": 0}

    # Step 2-4: 逐条增强
    db = SessionLocal()
    try:
        for p in problems:
            problem = db.query(Problem).filter_by(id=p["id"]).first()
            if not problem:
                continue

            # 分类增强（回退：保留提取器原始标签）
            try:
                new_ts, new_pt = classifier.classify_problem(
                    title=p.get("title", ""),
                    description=p.get("description", ""),
                    solution=p.get("solution", ""),
                )
                if new_ts:
                    problem.tech_stack = new_ts
                if new_pt:
                    problem.problem_type = new_pt
            except Exception:
                pass

            # 评分增强（回退：保留默认 5 分）
            try:
                scores = scorer.score_problem(
                    title=p.get("title", ""),
                    description=p.get("description", ""),
                    attempts=p.get("attempts", ""),
                    solution=p.get("solution", ""),
                )
                problem.priority_score = scores.get("total", 5)
            except Exception:
                pass

        db.commit()
    finally:
        db.close()

    # 向量索引（失败不影响返回）
    index_ok = sum(
        1 for p in problems
        if _safe_index(p["id"])
    )

    return {
        "count": len(problems),
        "problems": problems,
        "indexed": index_ok,
    }


def _safe_index(problem_id: int) -> bool:
    try:
        return vector_search.add_to_index(problem_id)
    except Exception:
        logging.exception("索引失败 problem_id=%s", problem_id)
        return False


# ══════════════════════════════════════════════════════════════════
# MCP Tools
# ══════════════════════════════════════════════════════════════════

@mcp.tool()
def search_experience(
    q: str,
    k: int = 5,
    tech: Optional[str] = None,
    project: Optional[str] = None,
    environment: Optional[dict] = None,
) -> dict:
    """
    双通道混合检索历史技术经验。

    用自然语言描述你遇到的问题，从经验库中检索最相似的解决方案。
    向量语义 + FTS5 关键词双通道检索，RRF 融合排序。
    支持环境过滤：传 environment={"os":"win11"} 会提升匹配经验的权重，不匹配的降权但不排除。

    参数:
        q: 查询文本（自然语言描述）
        k: 返回数量，默认 5
        tech: 按技术栈过滤，如 "Python"、"Docker"
        project: 限定项目范围
        environment: 当前运行环境 dict，如 {"os":"win11","python":"3.12"}
    """
    data = vector_search.search(
        query_text=q, k=k, tech_filter=tech, project_name=project,
        environment=environment,
    )
    # 记录搜索结果曝光（隐式反馈）
    result_ids = [r["problem_id"] for r in data["results"]]
    vector_search.record_search_impressions(result_ids)

    return {
        "query": q,
        "rewritten": data.get("_debug", {}).get("rewritten_query", q),
        "count": len(data["results"]),
        "results": data["results"],
    }


@mcp.tool()
def ingest_sessions(mode: str = "incremental") -> dict:
    """
    从 Claude JSONL 会话文件中自动摄入技术问题。

    扫描 ~/.claude/projects/ 目录，自动识别已结束的对话，
    提取技术问题并入库。

    参数:
        mode: 'incremental' 增量（只处理新会话），'full' 全量重建
    """
    if mode == "full":
        result = session_ingestor.ingest_all()
    else:
        result = session_ingestor.ingest_incremental()
    return {"mode": mode, **result}


@mcp.tool()
def ingest_status() -> dict:
    """查看 session 自动摄入的当前状态：已处理会话数、累计问题数、待处理数。"""
    return session_ingestor.get_ingest_status()


@mcp.tool()
def extract_from_text(conversation_text: str, project_name: str) -> dict:
    """
    手动粘贴一段 AI 编程对话，提取其中的技术问题并入库。

    用于处理非 Claude 对话（如 ChatGPT、Copilot Chat 等），
    或处理无法自动摄入的历史对话。

    参数:
        conversation_text: 完整的 AI 编程对话文本
        project_name: 所属项目名称
    """
    result = _run_extract_pipeline(conversation_text, project_name)
    return {
        "project": project_name,
        **result,
    }


@mcp.tool()
def save_problem(
    error: str,
    solution: str,
    attempts: Optional[list] = None,
    environment: Optional[dict] = None,
    project: Optional[str] = None,
    problem_type: Optional[str] = None,
    tech_stack: Optional[list] = None,
) -> dict:
    """
    结构化录入一个问题，跳过 LLM 提取步骤。适合已明确知道错误+解法的场景。

    参数:
        error: 错误描述或复现步骤
        solution: 最终解决方案
        attempts: 尝试过的方案列表，如 ["方案1","方案2"]
        environment: 运行环境，如 {"os":"win11","python":"3.12","docker":"26.1"}
        project: 项目名，不传默认 "Unknown"
        problem_type: 问题类型，不传则自动分类
        tech_stack: 技术栈列表，不传则自动分类
    """
    result = services.save_problem_service(
        error=error,
        solution=solution,
        attempts=attempts,
        environment=environment,
        project=project,
        problem_type=problem_type,
        tech_stack=tech_stack,
    )
    return result


@mcp.tool()
def record_feedback(problem_id: int, helpful: bool, note: Optional[str] = None) -> dict:
    """
    记录用户对某条经验的反馈，影响后续搜索排序。

    参数:
        problem_id: 问题 ID
        helpful: True 表示有用，False 表示没用
        note: 可选，备注为什么有用/没用
    """
    return services.record_feedback_service(problem_id, helpful, note)


@mcp.tool()
def list_problems(
    project: Optional[str] = None,
    tech: Optional[str] = None,
    min_score: Optional[int] = None,
    problem_type: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """
    按条件筛选问题列表，支持项目/技术栈/评分/类型组合筛选。

    参数:
        project: 按项目名筛选，如 "电商后台管理系统"
        tech: 按技术栈关键词筛选，如 "Docker"
        min_score: 最低优先级评分 (1-10)
        problem_type: 问题类型 — Bug/性能优化/架构决策/环境配置/API调试
        limit: 返回数量上限，默认 50
        offset: 分页偏移
    """
    db = SessionLocal()
    try:
        query = db.query(Problem)

        if project:
            proj = db.query(Project).filter_by(name=project).first()
            if proj:
                query = query.filter_by(project_id=proj.id)
            else:
                return {"total": 0, "problems": []}

        if tech:
            query = query.filter(Problem.tech_stack.contains(tech))
        if min_score is not None:
            query = query.filter(Problem.priority_score >= min_score)
        if problem_type:
            query = query.filter_by(problem_type=problem_type)

        total = query.count()
        problems = (
            query
            .order_by(Problem.priority_score.desc(), Problem.created_at.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )
        return {
            "total": total,
            "limit": limit,
            "offset": offset,
            "problems": [p.to_dict() for p in problems],
        }
    finally:
        db.close()


@mcp.tool()
def get_dashboard(project: Optional[str] = None) -> dict:
    """
    返回经验库统计摘要：总问题数、按类型/分数/技术栈分布、平均评分。

    参数:
        project: 可选，限定统计范围到指定项目
    """
    db = SessionLocal()
    try:
        query = db.query(Problem)
        if project:
            proj = db.query(Project).filter_by(name=project).first()
            if not proj:
                return {"message": "项目不存在", "project": project}
            query = query.filter_by(project_id=proj.id)

        problems = query.all()
        total = len(problems)
        if total == 0:
            return {
                "total": 0, "by_type": {}, "by_score_range": {},
                "top_tech": [], "avg_score": 0,
            }

        by_type = {}
        for p in problems:
            pt = p.problem_type or "未分类"
            by_type[pt] = by_type.get(pt, 0) + 1

        by_score_range = {"1-3": 0, "4-6": 0, "7-8": 0, "9-10": 0}
        for p in problems:
            s = p.priority_score or 5
            if s <= 3:
                by_score_range["1-3"] += 1
            elif s <= 6:
                by_score_range["4-6"] += 1
            elif s <= 8:
                by_score_range["7-8"] += 1
            else:
                by_score_range["9-10"] += 1

        tech_counts = {}
        for p in problems:
            if p.tech_stack:
                for t in p.tech_stack.split(","):
                    t = t.strip().lower()
                    if t:
                        tech_counts[t] = tech_counts.get(t, 0) + 1
        top_tech = sorted(tech_counts.items(), key=lambda x: x[1], reverse=True)[:10]

        avg_score = round(sum(p.priority_score or 0 for p in problems) / total, 1)

        return {
            "total": total,
            "by_type": by_type,
            "by_score_range": by_score_range,
            "top_tech": [{"tech": t, "count": c} for t, c in top_tech],
            "avg_score": avg_score,
        }
    finally:
        db.close()


@mcp.tool()
def rebuild_index() -> dict:
    """从 SQLite 数据库全量重建 ChromaDB 向量索引和 FTS5 全文索引。"""
    result = vector_search.rebuild_index()
    return {"message": "索引重建完成", **result}


@mcp.tool()
def generate_star(problem_id: int) -> dict:
    """
    为指定问题生成 STAR 面试故事（Situation / Task / Action / Result）。

    参数:
        problem_id: 问题 ID
    """
    import json

    db = SessionLocal()
    try:
        problem = db.query(Problem).filter_by(id=problem_id).first()
        if not problem:
            return {"error": "问题不存在"}

        if problem.star_story:
            vector_search.record_usage(problem_id)
            return {
                "problem_id": problem_id,
                "title": problem.title,
                "star": json.loads(problem.star_story),
                "cached": True,
            }

        result = star_gen.generate_star(problem_id)
        if not result:
            return {"error": "STAR 故事生成失败"}

        vector_search.record_usage(problem_id)
        return {
            "problem_id": problem_id,
            "title": problem.title,
            "star": result,
            "cached": False,
        }
    finally:
        db.close()


@mcp.tool()
def update_score(problem_id: int, score: int) -> dict:
    """
    手动更新指定问题的优先级评分。

    参数:
        problem_id: 问题 ID
        score: 新评分 (1-10)
    """
    if not 1 <= score <= 10:
        return {"error": "评分必须在 1-10 之间"}

    db = SessionLocal()
    try:
        problem = db.query(Problem).filter_by(id=problem_id).first()
        if not problem:
            return {"error": "问题不存在"}

        old_score = problem.priority_score
        problem.priority_score = score
        db.commit()
        return {
            "problem_id": problem_id,
            "old_score": old_score,
            "new_score": score,
        }
    finally:
        db.close()


@mcp.tool()
def run_reflection() -> dict:
    """
    触发 Rule-Maker 反思引擎。

    读取本周新增的技术问题，LLM 分析共性模式，
    生成平台无关的规则草案，写入 rules_suggestions.md。

    规则不直接覆写项目规则文件——需要人工 review 确认（Human-in-the-loop）。
    """
    result = rule_maker.run_reflection()
    return result


@mcp.tool()
def get_suggestions() -> dict:
    """
    查看当前待确认的规则建议草案。

    返回 rules_suggestions.md 的内容，
    包含 LLM 生成的规则、置信度、来源问题。
    """
    result = rule_maker.get_suggestions()
    return result


# ══════════════════════════════════════════════════════════════════
# 入口
# ══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="DevQuest MCP Server")
    parser.add_argument(
        "--http",
        action="store_true",
        help="以 HTTP 模式启动（开发调试用），默认 stdio 模式",
    )
    parser.add_argument(
        "--port", type=int, default=8000,
        help="HTTP 模式端口号（默认 8000）",
    )
    args = parser.parse_args()

    if args.http:
        mcp.settings.port = args.port
        mcp.run(transport="streamable-http")
    else:
        mcp.run(transport="stdio")
