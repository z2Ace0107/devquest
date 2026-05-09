# -*- coding: utf-8 -*-
"""
DevQuest Log — 问题提取引擎

使用 LangChain + DeepSeek API（兼容 OpenAI 格式）分析 AI 编程对话，
从中提取结构化技术问题，并存入数据库。

核心流程:
1. 接收对话文本 + 项目名
2. 调用 LLM 解析对话，输出严格 JSON
3. 解析 JSON，逐条写入 Problem 表
"""

import json
import os
import re
from typing import Optional

from dotenv import load_dotenv
load_dotenv()

from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage

from backend.database import SessionLocal
from backend.models import Project, Problem

# ── DeepSeek API 配置 ──────────────────────────────────────────
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "your-deepseek-api-key")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")

# ── 系统提示词 ──────────────────────────────────────────────────
SYSTEM_PROMPT = """你是一个资深技术复盘分析师。你的任务是从一段 AI 编程对话记录中，
识别出所有开发者遇到的技术问题，并提取结构化信息。

对于对话中每个独立的技术问题，你必须输出以下字段：
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


# ── LLM 客户端（单例延迟初始化）─────────────────────────────────
_llm: Optional[ChatOpenAI] = None


def _get_llm() -> ChatOpenAI:
    """获取或创建 DeepSeek LLM 客户端实例。"""
    global _llm
    if _llm is None:
        _llm = ChatOpenAI(
            model=DEEPSEEK_MODEL,
            api_key=DEEPSEEK_API_KEY,
            base_url=DEEPSEEK_BASE_URL,
            temperature=0.3,  # 低温度以保持输出稳定
        )
    return _llm


# ── 核心提取函数 ───────────────────────────────────────────────

def extract_problems(conversation_text: str, project_name: str) -> list[dict]:
    """
    从对话文本中提取技术问题并存入数据库。

    参数:
        conversation_text: AI 编程对话的完整文本
        project_name: 所属项目名称（如不存在则自动创建）

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
    return _save_to_db(problems_data, conversation_text, project_name)


def _call_llm_extract(conversation_text: str) -> str:
    """
    调用 LLM 分析对话，返回 JSON 字符串。
    """
    llm = _get_llm()
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
) -> list[dict]:
    """
    将提取的问题数据写入数据库。

    参数:
        problems_data: LLM 解析出的问题列表
        raw_conversation: 原始对话文本（完整保存用于溯源）
        project_name: 项目名称

    返回:
        list[dict]: 入库后的问题字典列表（含自增 id）
    """
    db = SessionLocal()
    try:
        # 获取或创建项目
        project = db.query(Project).filter_by(name=project_name).first()
        if not project:
            project = Project(name=project_name)
            db.add(project)
            db.flush()  # 获取 project.id

        result = []
        for item in problems_data:
            problem = Problem(
                project_id=project.id,
                title=item.get("title", ""),
                description=item.get("description", ""),
                attempts=json.dumps(
                    item.get("attempts", []), ensure_ascii=False
                ),
                solution=item.get("solution", ""),
                tech_stack=item.get("tech_stack", ""),
                problem_type=item.get("problem_type", ""),
                raw_conversation=raw_conversation,
            )
            db.add(problem)
            db.flush()  # 获取 problem.id
            result.append(problem.to_dict())

        db.commit()
        return result
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


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
