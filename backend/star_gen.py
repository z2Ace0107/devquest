# -*- coding: utf-8 -*-
"""
DevQuest — STAR 故事生成模块

将技术问题记录转换为面试可用的 STAR 故事（第一人称、口语化）:
- Situation: 当时在做什么
- Task: 遇到了什么问题
- Action: 采取了哪些步骤
- Result: 最终结果和成长收获

支持:
- generate_star(): 为指定问题生成 STAR 故事
- generate_all_stars(): 批量生成
"""

import json
from typing import Optional

from dotenv import load_dotenv
load_dotenv()

from langchain_core.messages import HumanMessage, SystemMessage

from backend.database import SessionLocal
from backend.models import Problem
from backend.llm_client import get_llm

# ── 系统提示词 ──────────────────────────────────────────────────
SYSTEM_PROMPT = """你是一个面试辅导专家。请根据给定的技术问题记录，生成一个面试用的 STAR 故事。

要求：
1. 以第一人称"我"来叙述，口语化、自然，像在面试中和面试官聊天
2. 控制在 200-400 字，简洁但有细节
3. 突出解决问题的思考过程，而不只是罗列步骤
4. Result 部分要体现个人成长和经验沉淀

输出一个 JSON 对象，包含 4 个字段：

{
  "situation": "当时我负责……（项目背景和上下文）",
  "task": "遇到的问题（具体描述）",
  "action": "我采取的解决步骤（按时间线叙述，体现思考）",
  "result": "最终结果（项目收益和个人成长）"
}

只输出 JSON，不要任何解释。"""


# ── 核心生成函数 ───────────────────────────────────────────────

def generate_star(problem_id: int) -> Optional[dict]:
    """
    为数据库中指定问题生成 STAR 故事，结果写入 star_story 字段。

    参数:
        problem_id: 问题的数据库 ID

    返回:
        dict or None: 生成的 STAR 故事（含 s/t/a/r 四个字段）
    """
    db = SessionLocal()
    try:
        problem = db.query(Problem).filter_by(id=problem_id).first()
        if not problem:
            return None

        star = _generate_star_text(
            title=problem.title or "",
            description=problem.description or "",
            attempts=problem.attempts or "",
            solution=problem.solution or "",
        )

        # 存入数据库
        problem.star_story = json.dumps(star, ensure_ascii=False)
        db.commit()
        return star
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def generate_star_text(
    title: str = "",
    description: str = "",
    attempts: str = "",
    solution: str = "",
) -> dict:
    """
    纯文本输入生成 STAR 故事（不依赖数据库）。

    参数:
        title: 问题标题
        description: 问题描述
        attempts: 尝试过的方案
        solution: 最终解决方案

    返回:
        dict: {"situation", "task", "action", "result"}
    """
    try:
        return _generate_star_text(title, description, attempts, solution)
    except Exception:
        return _fallback_star(title, description, solution)


def _generate_star_text(
    title: str, description: str, attempts: str, solution: str
) -> dict:
    """调用 LLM 生成 STAR 故事。"""
    llm = get_llm(temperature=0.3)

    # 构建上下文
    parts = []
    if title:
        parts.append(f"问题标题：{title}")
    if description:
        parts.append(f"问题背景：{description}")
    if attempts:
        try:
            attempt_list = json.loads(attempts)
            parts.append(f"尝试过的方案：{'；'.join(attempt_list)}")
        except (json.JSONDecodeError, TypeError):
            parts.append(f"尝试过的方案：{attempts}")
    if solution:
        parts.append(f"最终解决方案：{solution}")

    user_prompt = "\n".join(parts)

    messages = [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=user_prompt),
    ]

    response = llm.invoke(messages)
    return _parse_star(response.content)


def _parse_star(raw: str) -> dict:
    """解析 LLM 返回的 STAR JSON。"""
    import re

    raw = re.sub(r"```(?:json)?\s*", "", raw)
    raw = re.sub(r"```", "", raw)
    raw = raw.strip()

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*?\}", raw, re.DOTALL)
        if match:
            data = json.loads(match.group())
        else:
            return _fallback_star("", "", "")

    return {
        "situation": data.get("situation", ""),
        "task": data.get("task", ""),
        "action": data.get("action", ""),
        "result": data.get("result", ""),
    }


def _fallback_star(title: str, description: str, solution: str) -> dict:
    """LLM 不可用时的模板兜底。"""
    return {
        "situation": f"在项目开发过程中，我负责相关模块的开发和维护。",
        "task": title or description or "遇到一个技术问题需要排查和解决。",
        "action": solution or "经过分析和调试，找到了问题的根因并实施了修复方案。",
        "result": "问题得到解决，对相关技术有了更深入的理解，后续遇到同类问题能快速定位。",
    }


# ── 批量操作 ────────────────────────────────────────────────────

def generate_all_stars(project_name: Optional[str] = None) -> int:
    """
    为数据库中所有未生成 STAR 故事的问题批量生成。

    参数:
        project_name: 可选，仅处理指定项目

    返回:
        int: 生成的故事数量
    """
    db = SessionLocal()
    try:
        query = db.query(Problem).filter(
            (Problem.star_story.is_(None)) | (Problem.star_story == "")
        )
        if project_name:
            from backend.models import Project
            project = db.query(Project).filter_by(name=project_name).first()
            if not project:
                return 0
            query = query.filter_by(project_id=project.id)

        problems = query.all()
        count = 0
        for problem in problems:
            star = _generate_star_text(
                title=problem.title or "",
                description=problem.description or "",
                attempts=problem.attempts or "",
                solution=problem.solution or "",
            )
            problem.star_story = json.dumps(star, ensure_ascii=False)
            count += 1

        db.commit()
        return count
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
