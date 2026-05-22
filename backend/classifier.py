# -*- coding: utf-8 -*-
"""
DevQuest — 技术标签自动分类

对已提取的问题进行二次分类，标准化 tech_stack 和 problem_type 字段。
支持:
- 单条分类: classify_problem()
- 批量重分类: reclassify_all()
- 规则兜底: _rule_based_classify()
"""

from dotenv import load_dotenv
load_dotenv()

from langchain_core.messages import HumanMessage, SystemMessage

from backend.database import SessionLocal
from backend.models import Problem
from backend.llm_client import get_llm

# ── 已知技术栈词表（用于标准化）─────────────────────────────────
KNOWN_TECH = {
    "python", "fastapi", "flask", "django", "streamlit",
    "sqlalchemy", "sqlite", "postgresql", "mysql", "mongodb", "redis",
    "chromadb", "langchain", "openai", "deepseek",
    "docker", "kubernetes", "nginx", "celery",
    "react", "vue", "javascript", "typescript", "html", "css",
    "git", "github actions", "ci/cd",
    "pytest", "unittest",
    "pydantic", "asyncio", "websocket",
    "prometheus", "grafana",
}

# ── 问题类型枚举 ───────────────────────────────────────────────
PROBLEM_TYPES = ["Bug", "性能优化", "架构决策", "环境配置", "API调试"]

# ── 系统提示词 ──────────────────────────────────────────────────
SYSTEM_PROMPT = """你是一个技术分类专家。请根据问题信息，输出标准化的技术标签和问题类型。

输出要求：
- 严格输出一个单行 JSON 对象，包含 tech_stack 和 problem_type 两个字段
- tech_stack: 逗号分隔的技术栈字符串，只使用常见技术名词，例如 "Python,FastAPI,Docker,SQLAlchemy"
- problem_type: 必须是以下五选一：Bug、性能优化、架构决策、环境配置、API调试

只输出 JSON，不要任何解释。

示例输出：
{"tech_stack": "Python,FastAPI,Docker", "problem_type": "环境配置"}"""


# ── 核心分类函数 ───────────────────────────────────────────────

def classify_problem(
    title: str = "",
    description: str = "",
    solution: str = "",
) -> tuple[str, str]:
    """
    对单个问题进行技术标签和类型分类。

    参数:
        title: 问题标题
        description: 问题描述
        solution: 解决方案

    返回:
        tuple[str, str]: (tech_stack, problem_type)
    """
    # 优先使用 LLM 分类
    try:
        return _llm_classify(title, description, solution)
    except Exception:
        # LLM 不可用时降级为规则分类
        return _rule_based_classify(title, description, solution)


def _llm_classify(title: str, description: str, solution: str) -> tuple[str, str]:
    """调用 LLM 进行分类。"""
    llm = get_llm(temperature=0.1)
    user_prompt = (
        f"请对以下技术问题进行分类：\n\n"
        f"标题：{title}\n"
        f"描述：{description}\n"
        f"解决方案：{solution}"
    )

    messages = [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=user_prompt),
    ]

    response = llm.invoke(messages)
    return _parse_classification(response.content)


def _parse_classification(raw: str) -> tuple[str, str]:
    """解析 LLM 返回的分类 JSON。"""
    import json
    import re

    # 去掉 markdown 包裹
    raw = re.sub(r"```(?:json)?\s*", "", raw)
    raw = re.sub(r"```", "", raw)
    raw = raw.strip()

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # 尝试提取 {...}
        match = re.search(r"\{.*?\}", raw, re.DOTALL)
        if match:
            data = json.loads(match.group())
        else:
            return ("", "")

    tech_stack = _normalize_tech_stack(data.get("tech_stack", ""))
    problem_type = data.get("problem_type", "")
    if problem_type not in PROBLEM_TYPES:
        problem_type = ""
    return (tech_stack, problem_type)


def _normalize_tech_stack(raw: str) -> str:
    """
    标准化技术栈字符串：
    - 去除多余空格
    - 过滤无效标签
    - 统一大小写
    """
    if not raw:
        return ""

    tags = [t.strip() for t in raw.split(",") if t.strip()]
    # 只保留已知技术栈中的标签（允许未知标签通过，不做强制过滤）
    normalized = []
    for tag in tags:
        tag_lower = tag.lower()
        # 尝试匹配已知标签
        matched = None
        for known in KNOWN_TECH:
            if tag_lower == known or tag_lower in known or known in tag_lower:
                matched = known.title() if known in {"api", "ci/cd"} else known
                break
        normalized.append(matched if matched else tag)
    return ",".join(normalized)


def _rule_based_classify(
    title: str, description: str, solution: str
) -> tuple[str, str]:
    """
    基于关键词的规则兜底分类（无需 LLM）。
    """
    combined = f"{title} {description} {solution}".lower()

    # 技术栈关键词匹配
    tech_matches = []
    for tech in sorted(KNOWN_TECH, key=len, reverse=True):
        if tech in combined:
            tech_matches.append(tech)

    # 问题类型关键词匹配
    type_keywords = {
        "Bug": ["bug", "报错", "错误", "异常", "崩溃", "失败", "error", "exception"],
        "性能优化": ["慢", "性能", "优化", "卡顿", "超时", "内存", "cpu", "延迟", "qps"],
        "架构决策": ["架构", "设计", "选型", "重构", "拆分", "单体", "微服务", "模块化"],
        "环境配置": ["部署", "配置", "环境", "docker", "容器", "nginx", "安装", "启动"],
        "API调试": ["api", "接口", "请求", "响应", "参数", "curl", "postman", "restful"],
    }

    scores = {}
    for ptype, keywords in type_keywords.items():
        scores[ptype] = sum(1 for kw in keywords if kw in combined)

    best_type = max(scores, key=scores.get) if max(scores.values()) > 0 else ""

    return (",".join(tech_matches), best_type)


# ── 批量操作 ────────────────────────────────────────────────────

def reclassify_all(project_name: Optional[str] = None) -> int:
    """
    对数据库中已有问题重新分类，更新 tech_stack 和 problem_type。

    参数:
        project_name: 可选，仅重分类指定项目的问题；为 None 则处理全部

    返回:
        int: 重新分类的问题数量
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
            tech_stack, problem_type = classify_problem(
                title=problem.title or "",
                description=problem.description or "",
                solution=problem.solution or "",
            )
            if tech_stack:
                problem.tech_stack = tech_stack
            if problem_type:
                problem.problem_type = problem_type
            count += 1

        db.commit()
        return count
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
