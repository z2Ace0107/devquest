# -*- coding: utf-8 -*-
"""
DevQuest — 优先级评分模块

根据四个维度对问题自动评分（1-10），支持:
- LLM 评分: 语义理解，更准确
- 规则评分: 基于文本特征，无需 API
- 批量评分: score_all()

四个评分维度:
1. 问题复杂度 — 问题涉及的技术深度和广度
2. 解决过程曲折度 — 尝试方案数量、是否反复试错
3. 项目影响力 — 问题对项目进度/架构的影响程度
4. 技术栈广度 — 涉及的技术数量
"""

from dotenv import load_dotenv
load_dotenv()

from langchain_core.messages import HumanMessage, SystemMessage

from backend.database import SessionLocal
from backend.models import Problem
from backend.llm_client import get_llm

# ── 维度权重（可配置）───────────────────────────────────────────
DIMENSION_WEIGHTS = {
    "complexity": 0.30,   # 问题复杂度
    "tortuosity": 0.25,   # 解决曲折度
    "impact": 0.25,       # 项目影响力
    "tech_breadth": 0.20, # 技术栈广度
}

# ── 系统提示词 ──────────────────────────────────────────────────
SYSTEM_PROMPT = """你是一个资深技术评估专家。请根据问题信息，从四个维度打分（每项 1-10 分）：

1. complexity（问题复杂度）：技术深度 + 排查难度
2. tortuosity（解决曲折度）：尝试了几种方案，是否反复试错
3. impact（项目影响力）：对项目进度/架构/团队的影响程度
4. tech_breadth（技术栈广度）：涉及多少种不同技术

输出要求：
- 严格输出一个 JSON 对象，包含上述 4 个字段，再加一个 total（加权总分，四舍五入到整数）
- 加权公式：total = round(complexity*0.30 + tortuosity*0.25 + impact*0.25 + tech_breadth*0.20)

只输出 JSON，不要任何解释。

示例输出：
{"complexity": 7, "tortuosity": 6, "impact": 8, "tech_breadth": 5, "total": 7}"""


# ── LLM 客户端（统一） ──────────────────────────────────


# ── 评分函数 ────────────────────────────────────────────────────
# ── 核心评分函数 ───────────────────────────────────────────────

def score_problem(
    title: str = "",
    description: str = "",
    attempts: str = "",
    solution: str = "",
) -> dict:
    """
    对单个问题进行四维度评分。

    参数:
        title: 问题标题
        description: 问题描述
        attempts: 尝试过的方案（JSON 数组字符串或纯文本）
        solution: 最终解决方案

    返回:
        dict: {"complexity", "tortuosity", "impact", "tech_breadth", "total"}
    """
    try:
        return _llm_score(title, description, attempts, solution)
    except Exception:
        return _rule_score(title, description, attempts, solution)


def _llm_score(
    title: str, description: str, attempts: str, solution: str
) -> dict:
    """调用 LLM 进行四维度评分。"""
    llm = get_llm(temperature=0.2)

    # 构建评分请求
    parts = []
    if title:
        parts.append(f"标题：{title}")
    if description:
        parts.append(f"描述：{description}")
    if attempts:
        # attempts 可能是 JSON 数组，尝试格式化
        try:
            attempt_list = json.loads(attempts)
            parts.append(f"尝试方案：{'; '.join(attempt_list)}")
        except (json.JSONDecodeError, TypeError):
            parts.append(f"尝试方案：{attempts}")
    if solution:
        parts.append(f"解决方案：{solution}")

    user_prompt = "请对以下技术问题进行评分：\n\n" + "\n".join(parts)

    messages = [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=user_prompt),
    ]

    response = llm.invoke(messages)
    return _parse_score(response.content)


def _parse_score(raw: str) -> dict:
    """解析 LLM 返回的评分 JSON。"""
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
            return _default_score()

    # 校验并取整
    keys = ["complexity", "tortuosity", "impact", "tech_breadth"]
    result = {}
    for k in keys:
        val = data.get(k, 5)
        result[k] = max(1, min(10, int(val)))

    # 计算加权总分
    total = sum(
        result[k] * DIMENSION_WEIGHTS[k]
        for k in keys
    )
    result["total"] = round(total)
    return result


def _rule_score(
    title: str, description: str, attempts: str, solution: str
) -> dict:
    """
    基于文本特征的规则评分（离线可用）。

    启发式规则:
    - 复杂度: 基于描述长度和关键词（并发、分布式、算法 等）
    - 曲折度: 基于 attempts 中方案数量
    - 影响力: 基于关键词（阻塞、线上、生产、核心 等）
    - 技术广度: 基于 solution 中技术关键词出现数量
    """
    combined = f"{title} {description}".lower()
    solution_lower = (solution or "").lower()

    # ── 复杂度（1-10）─────────────────────────────────────
    complexity_keywords = [
        "并发", "分布式", "算法", "递归", "线程安全", "内存泄漏",
        "死锁", "事务", "一致性", "高并发", "大数据", "锁",
        "异步", "协程", "多线程", "竞态", "缓存一致",
    ]
    complexity_hits = sum(1 for kw in complexity_keywords if kw in combined)
    # 描述越长通常问题越复杂
    desc_score = min(5, len(description) // 100)
    complexity = max(1, min(10, 3 + complexity_hits * 2 + desc_score))

    # ── 曲折度（1-10）─────────────────────────────────────
    try:
        attempt_list = json.loads(attempts)
        attempt_count = len(attempt_list) if isinstance(attempt_list, list) else 1
    except (json.JSONDecodeError, TypeError):
        # 用换行或分号估算尝试次数
        attempt_count = max(1, len(attempts.split("\n")), len(attempts.split("；")))
    # 每多一次尝试 +2 分
    tortuosity = max(1, min(10, 2 + attempt_count * 2))

    # ── 影响力（1-10）─────────────────────────────────────
    impact_keywords = [
        "阻塞", "线上", "生产", "核心", "崩溃", "宕机", "不可用",
        "关键", "紧急", "严重", "全部", "大量", "用户",
    ]
    impact_hits = sum(1 for kw in impact_keywords if kw in combined)
    impact = max(1, min(10, 3 + impact_hits * 2))

    # ── 技术广度（1-10）────────────────────────────────────
    tech_keywords = [
        "python", "fastapi", "flask", "django", "docker", "kubernetes",
        "nginx", "redis", "celery", "sqlalchemy", "postgresql", "mysql",
        "mongodb", "langchain", "chromadb", "streamlit", "asyncio",
        "react", "vue", "typescript", "javascript", "websocket",
        "prometheus", "grafana", "git", "ci/cd", "pytest",
    ]
    tech_hits = sum(1 for kw in tech_keywords if kw in solution_lower)
    tech_breadth = max(1, min(10, 1 + tech_hits))

    total = round(
        complexity * DIMENSION_WEIGHTS["complexity"]
        + tortuosity * DIMENSION_WEIGHTS["tortuosity"]
        + impact * DIMENSION_WEIGHTS["impact"]
        + tech_breadth * DIMENSION_WEIGHTS["tech_breadth"]
    )

    return {
        "complexity": complexity,
        "tortuosity": tortuosity,
        "impact": impact,
        "tech_breadth": tech_breadth,
        "total": total,
    }


def _default_score() -> dict:
    return {
        "complexity": 5,
        "tortuosity": 5,
        "impact": 5,
        "tech_breadth": 5,
        "total": 5,
    }


# ── 批量操作 ────────────────────────────────────────────────────

def score_all(project_name: Optional[str] = None) -> int:
    """
    对数据库中所有未评分问题（priority_score=5 默认值）进行评分，
    更新 priority_score 字段。

    参数:
        project_name: 可选，仅处理指定项目

    返回:
        int: 评分的问题数量
    """
    db = SessionLocal()
    try:
        query = db.query(Problem)
        if project_name:
            from backend.models import Project
            project = db.query(Project).filter_by(name=project_name).first()
            if not project:
                return 0
            query = query.filter_by(project_id=project.id)

        problems = query.all()
        count = 0
        for problem in problems:
            result = score_problem(
                title=problem.title or "",
                description=problem.description or "",
                attempts=problem.attempts or "",
                solution=problem.solution or "",
            )
            problem.priority_score = result["total"]
            count += 1

        db.commit()
        return count
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
