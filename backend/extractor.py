# -*- coding: utf-8 -*-
"""
DevQuest — 问题提取引擎

使用 LangChain + LLM（通过统一 LLM 客户端）分析 AI 编程对话，
从中提取结构化技术问题，并存入数据库。

核心流程:
1. 接收对话文本 + 项目名
2. 调用 LLM 解析对话，输出严格 JSON
3. 解析 JSON，逐条写入 Problem 表
"""

import json
import os
from datetime import datetime, timezone
import re

from pathlib import Path as _Path
from dotenv import load_dotenv
load_dotenv(_Path(__file__).resolve().parent.parent / ".env")

from langchain_core.messages import HumanMessage, SystemMessage

from backend.database import SessionLocal
from backend.models import Project, Problem
from backend.llm_client import get_llm

# ── 关键提示词 ──────────────────────────────────────────────────
SYSTEM_PROMPT = """你是一个资深技术复盘分析师。你的任务是从一段 AI 编程对话记录中，
识别出所有开发者遇到的技术问题，并提取结构化信息。

对于对话中每个独立的技术问题，你必须输出以下字段。

**重要：如何判断"独立问题"**——只要根因不同或解决方案不同，就应拆分为独立问题。即使多个问题涉及同一个组件、同一个报错信息，甚至是同一段对话中连续讨论的，也请分别提取。宁可拆分过细，不要合并。例如：同一个 Table 组件的"渲染慢"和"hover 闪烁"是两个问题（根因不同），应输出两条记录。
- title: 问题标题（一句话总结，不超过 30 字）
- description: 问题详细描述，包含上下文背景（100-300 字）
- attempts: 尝试过的方案列表，每个方案用一句话描述。如果对话中提到了多次尝试，请全部列出
- solution: 最终解决方案，描述具体步骤（100-500 字）。如果对话中未给出解决方案，标注"未解决"
- tech_stack: 涉及的技术栈，用逗号分隔，例如 "Python,FastAPI,SQLAlchemy,SQLite"
- problem_type: 问题类型，必须是以下之一：Bug、性能优化、架构决策、环境配置、API调试

输出格式要求：
- 严格输出一个 JSON 数组，不要包含任何其他文字
- 每个元素是一个 JSON 对象，包含上述 6 个字段
- 如果对话中没有发现任何技术问题，输出空数组 []
- attempts 字段必须是 JSON 字符串数组，例如 ["尝试了方案A", "尝试了方案B"]

示例输出：
[
  {
    "title": "FastAPI 异步任务超时问题",
    "description": "在使用 FastAPI 后台任务发送邮件时，出现了请求超时的现象……",
    "attempts": ["尝试增大超时时间", "尝试使用 asyncio.create_task"],
    "solution": "改用 Celery 队列异步处理邮件发送，配置 Redis 作为 broker……",
    "tech_stack": "Python,FastAPI,Celery,Redis",
    "problem_type": "Bug"
  }
]"""


# ── LLM 客户端（统一）─────────────────────────────────
# 使用 backend.llm_client.get_llm()，支持主备自动切换


# ── 核心提取函数 ───────────────────────────────────────────────

def extract_problems(
    conversation_text: str,
    project_name: str,
    session_id: str = None,
) -> list[dict]:
    """
    从对话文本中提取技术问题并存入数据库。

    参数:
        conversation_text: AI 编程对话的完整文本
        project_name: 所属项目名称（如不存在则自动创建）
        session_id: 来源会话 ID（用于溯源，如 JSONL 文件名）

    返回:
        list[dict]: 提取到的问题记录列表（已入库，包含 id）
    """
    # 1. 调用 LLM 解析对话
    raw_json = _call_llm_extract(conversation_text)

    # 2. 解析 JSON
    problems_data = _parse_json_safely(raw_json)

    if not problems_data:
        return []

    # 3. 存入数据库
    return _save_to_db(problems_data, conversation_text, project_name, session_id)


def _call_llm_extract(conversation_text: str) -> str:
    """
    调用 LLM 分析对话，返回 JSON 字符串。
    """
    llm = get_llm(temperature=0.3)
    user_prompt = f"请分析以下 AI 编程对话记录，提取所有技术问题：\n\n{conversation_text}"

    messages = [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=user_prompt),
    ]

    response = llm.invoke(messages)
    return response.content


def _parse_json_safely(raw: str) -> list[dict]:
    """
    安全解析 LLM 返回的 JSON，兼容以下情况：
    - 被 ```json ... ``` 包裹
    - 被 ``` ... ``` 包裹
    - 前后有多余空白或说明文字
    """
    if not raw or not raw.strip():
        return []

    # 尝试去掉 markdown 代码块包裹
    code_block_pattern = r"```(?:json)?\s*\n?(.*?)\n?```"
    match = re.search(code_block_pattern, raw, re.DOTALL)
    if match:
        raw = match.group(1).strip()

    # 尝试直接解析
    try:
        data = json.loads(raw.strip())
        if isinstance(data, list):
            return data
        # 有时候 LLM 返回单个对象而非数组
        if isinstance(data, dict):
            return [data]
    except json.JSONDecodeError:
        pass

    # 兜底：尝试找到第一个 [ 和最后一个 ] 之间的内容
    try:
        start = raw.index("[")
        end = raw.rindex("]") + 1
        data = json.loads(raw[start:end])
        if isinstance(data, list):
            return data
    except (ValueError, json.JSONDecodeError):
        pass

    return []


def _save_to_db(
    problems_data: list[dict],
    raw_conversation: str,
    project_name: str,
    session_id: str = None,
) -> list[dict]:
    """
    将提取的问题数据写入数据库。入库前进行语义去重：
    新问题与已有问题库中最近匹配的余弦距离 < 0.125 时，合并而非新增。

    参数:
        problems_data: LLM 解析出的问题列表
        raw_conversation: 原始对话文本（完整保存用于溯源）
        project_name: 项目名称
        session_id: 来源会话 ID（用于溯源）

    返回:
        list[dict]: 入库后的问题字典列表（含自增 id）
    """
    from backend import vector_search

    db = SessionLocal()
    try:
        project = db.query(Project).filter_by(name=project_name).first()
        if not project:
            project = Project(name=project_name)
            db.add(project)
            db.flush()

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        merged_count = 0
        result = []
        for item in problems_data:
            title = item.get("title", "")
            description = item.get("description", "")

            # ── 语义去重检查 ─────────────────────────────────
            dup_id, dup_dist = vector_search.search_similar(title, description)

            if dup_id is not None and dup_dist < vector_search.DEDUP_THRESHOLD:
                # 合并到已有问题
                existing = db.query(Problem).filter_by(id=dup_id).first()
                if existing:
                    _merge_problem(existing, item, raw_conversation)
                    result.append(existing.to_dict())
                    merged_count += 1
                    continue

            # 新建问题
            problem = Problem(
                project_id=project.id,
                title=title,
                description=description,
                attempts=json.dumps(
                    item.get("attempts", []), ensure_ascii=False
                ),
                solution=item.get("solution", ""),
                tech_stack=item.get("tech_stack", ""),
                problem_type=item.get("problem_type", ""),
                raw_conversation=raw_conversation,
                first_seen_at=now,
                source_session_id=session_id,
                captured_at=now,
            )
            db.add(problem)
            db.flush()  # 获取 problem.id
            result.append(problem.to_dict())

        db.commit()

        # 写入日志（静默，不打断返回）
        if merged_count > 0:
            import logging
            logging.getLogger(__name__).info(
                f"去重: {merged_count}/{len(problems_data)} 个问题合并到已有记录"
            )

        return result
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _merge_problem(existing: Problem, new_item: dict, raw_conversation: str):
    """
    将新提取的问题内容合并到已有问题记录。

    合并策略:
    - attempts: 追加新的尝试方案，去重
    - solution: 如果新方案更长更详细，替换旧方案
    - tech_stack: 合并技术栈标签
    - raw_conversation: 追加新的对话片段
    - title/description/problem_type: 保留原有（避免覆盖人工修正）
    """
    import json as _json

    # 合并 attempts
    try:
        old_attempts = _json.loads(existing.attempts or "[]")
    except (_json.JSONDecodeError, TypeError):
        old_attempts = []

    new_attempts = new_item.get("attempts", [])
    if isinstance(new_attempts, str):
        try:
            new_attempts = _json.loads(new_attempts)
        except (_json.JSONDecodeError, TypeError):
            new_attempts = [new_attempts] if new_attempts else []

    seen = set(old_attempts)
    for a in new_attempts:
        if a and a not in seen:
            old_attempts.append(a)
            seen.add(a)
    existing.attempts = _json.dumps(old_attempts, ensure_ascii=False)

    # 合并 solution：新方案更长时替换
    new_sol = new_item.get("solution", "")
    old_sol = existing.solution or ""
    if len(new_sol) > len(old_sol):
        existing.solution = new_sol

    # 合并 tech_stack：去重拼接
    old_tech = set(t.strip() for t in (existing.tech_stack or "").split(",") if t.strip())
    new_tech = set(t.strip() for t in new_item.get("tech_stack", "").split(",") if t.strip())
    existing.tech_stack = ",".join(sorted(old_tech | new_tech))

    # 追加原始对话
    if raw_conversation and raw_conversation not in (existing.raw_conversation or ""):
        existing.raw_conversation = (existing.raw_conversation or "") + "\n---\n" + raw_conversation

    # 解法版本 +1
    existing.solution_version = (existing.solution_version or 0) + 1

    # 更新最后捕获时间
    existing.captured_at = datetime.now(timezone.utc).replace(tzinfo=None)


# ── 便捷函数：从文件导入 ───────────────────────────────────────

def extract_from_file(file_path: str, project_name: str) -> list[dict]:
    """
    从文本文件读取对话内容并提取问题。

    参数:
        file_path: 对话文本文件的路径
        project_name: 所属项目名称

    返回:
        list[dict]: 提取到的问题记录列表
    """
    with open(file_path, "r", encoding="utf-8") as f:
        conversation_text = f.read()
    return extract_problems(conversation_text, project_name)


# ── 飞书归档压缩 ──────────────────────────────────────────────

def compress_after_feishu_archive(
    problem_ids: list[int],
    feishu_doc_id: str,
    feishu_url: str = None,
) -> dict:
    """
    将已推送到飞书的 Problem 压缩本地存储。

    策略:
    - raw_conversation → 只保留 "[已归档] 完整内容见飞书文档 {doc_id}"
    - description → 截断至前 200 字 + 飞书链接
    - solution → 替换为 "[已归档] → {doc_url}"
    - feishu_archived → 标记为 1
    - title/tech_stack/problem_type/score/environment 全部保留（搜索依赖）

    压缩后向 ChromaDB 更新文档文本，保持搜索结果与存储一致。
    """
    import logging
    from backend.database import SessionLocal
    from backend.models import Problem

    logger = logging.getLogger(__name__)
    db = SessionLocal()
    truncated = 0
    total_bytes_before = 0
    total_bytes_after = 0

    try:
        problems = db.query(Problem).filter(Problem.id.in_(problem_ids)).all()
        if not problems:
            return {"archived": 0, "message": "无匹配 Problem"}

        url = feishu_url or f"f'{feishu_doc_id}'"
        archive_notice = f"[已归档] 完整内容见飞书文档 {url}"

        for p in problems:
            # 计算压缩前大小
            for field in ["raw_conversation", "description", "solution"]:
                val = getattr(p, field, "") or ""
                total_bytes_before += len(val.encode("utf-8"))

            # 压缩
            old_desc = p.description or ""
            p.raw_conversation = archive_notice
            p.description = (old_desc[:200] + f"...\n\n{archive_notice}") if len(old_desc) > 200 else old_desc
            p.solution = f"[已归档] → {url}"
            p.feishu_archived = 1

            # 计算压缩后大小
            for field in ["raw_conversation", "description", "solution"]:
                val = getattr(p, field, "") or ""
                total_bytes_after += len(val.encode("utf-8"))

            truncated += 1

        db.commit()
        size_saved_kb = round((total_bytes_before - total_bytes_after) / 1024)

        logger.info("飞书归档压缩: %d 条经验 → 释放约 %d KB", truncated, size_saved_kb)
        return {
            "archived": truncated,
            "feishu_doc_id": feishu_doc_id,
            "size_saved_kb": size_saved_kb,
            "message": f"已压缩 {truncated} 条，释放约 {size_saved_kb} KB。完整内容: {url}",
        }
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
