# -*- coding: utf-8 -*-
"""
DevQuest — Rule-Maker 反思引擎

从 SQLite 读取本周新增问题，LLM 反思提取共性模式，
生成平台无关的规则草案，可注入 Claude Code（CLAUDE.md）、Cursor（.cursorrules）等主流 AI 编程工具。

安全设计：规则写入 rules_suggestions.md，不直接覆写项目规则文件。
用户手动 review 后确认合并（Human-in-the-loop）。
"""

import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

BASE_DIR = Path(__file__).resolve().parent.parent

from dotenv import load_dotenv

load_dotenv(BASE_DIR / ".env")

from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage

from backend.database import SessionLocal
from backend.models import Problem

# ── 配置 ───────────────────────────────────────────────────────
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")

SUGGESTIONS_FILE = BASE_DIR / "rules_suggestions.md"

SYSTEM_PROMPT = """你是一个资深技术导师，负责分析开发者一周内遇到的技术问题，提取可复用的开发规则。

你的任务是：
1. 从问题列表中识别反复出现的模式（相同类型的报错、相同的知识盲区、相同的架构偏好）
2. 提炼出 3-5 条具体可执行的开发规则
3. 规则要具体——"记得写 try/except"太笼统，"涉及文件 I/O 和网络请求的函数必须加 try/except 并 logging 错误"才是好规则

输出格式（JSON）：
{
  "summary": "一句话总结本周技术模式",
  "rules": [
    {
      "rule": "规则内容（一句话，具体可执行）",
      "category": "error_handling | code_style | architecture | tooling | testing",
      "confidence": 0.9,
      "source_problems": [1, 3, 5],
      "rationale": "为什么这条规则值得提炼——基于什么反复出现的问题"
    }
  ]
}

规则撰写原则：
- 每条规则必须能从本周至少 2 个问题中推导出来
- confidence 基于问题出现频率和严重程度：4+次 → 0.9+，2-3次 → 0.7-0.8
- category 要准确：报错/异常 → error_handling，代码风格/命名 → code_style，架构选择 → architecture，工具/环境 → tooling，测试相关 → testing
- 优先提炼评分 7+ 的问题中的模式

只输出 JSON，不要任何解释。"""

# ── LLM 客户端 ──────────────────────────────────────────────────
_llm: Optional[ChatOpenAI] = None


def _get_llm() -> ChatOpenAI:
    global _llm
    if _llm is None:
        _llm = ChatOpenAI(
            model=DEEPSEEK_MODEL,
            api_key=DEEPSEEK_API_KEY,
            base_url=DEEPSEEK_BASE_URL,
            temperature=0.3,
        )
    return _llm


# ── 数据查询 ───────────────────────────────────────────────────

def _get_week_problems() -> list[dict]:
    """查询本周新增的问题记录，按评分降序。"""
    now = datetime.utcnow()
    week_start = now - timedelta(days=now.weekday())
    week_start = week_start.replace(hour=0, minute=0, second=0, microsecond=0)

    db = SessionLocal()
    try:
        problems = (
            db.query(Problem)
            .filter(Problem.created_at >= week_start)
            .order_by(Problem.priority_score.desc(), Problem.created_at.desc())
            .all()
        )
        return [p.to_dict() for p in problems]
    finally:
        db.close()


def _format_problems_for_reflection(problems: list[dict]) -> str:
    """将问题列表格式化为 LLM 反思输入。"""
    if not problems:
        return "本周暂无新增技术问题。"

    lines = []
    for i, p in enumerate(problems, 1):
        proj = p.get('project_name', '')
        proj_label = f" [{proj}]" if proj else ""
        lines.append(
            f"#{p['id']}{proj_label} [{p.get('problem_type', '未知')}] "
            f"{p.get('title', '无标题')} (评分 {p.get('priority_score', 5)}/10)"
        )
        if p.get("description"):
            desc = p["description"]
            if len(desc) > 300:
                desc = desc[:300] + "..."
            lines.append(f"  描述: {desc}")
        if p.get("solution"):
            sol = p["solution"]
            if len(sol) > 300:
                sol = sol[:300] + "..."
            lines.append(f"  方案: {sol}")
        if p.get("tech_stack"):
            lines.append(f"  技术栈: {p['tech_stack']}")
        lines.append("")
    return "\n".join(lines)


# ── 核心反思逻辑 ───────────────────────────────────────────────

def run_reflection(project_name: Optional[str] = None) -> dict:
    """
    执行一次反思：读取本周问题 → LLM 分析 → 生成规则草案 → 写入建议文件。

    参数:
        project_name: 可选，限定项目范围

    返回:
        dict: {"week_start": str, "problem_count": int, "rules": list, "file_path": str}
    """
    problems = _get_week_problems()

    if project_name:
        problems = [p for p in problems if p.get("project") == project_name]

    if len(problems) < 2:
        return {
            "week_start": _week_start_str(),
            "problem_count": len(problems),
            "rules": [],
            "message": "本周问题数量不足（需至少 2 个），跳过反思",
        }

    problems_text = _format_problems_for_reflection(problems)
    week_label = _week_label()

    llm = _get_llm()
    messages = [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(
            content=f"本周时间范围：{week_label}\n\n"
            f"本周技术问题列表（共 {len(problems)} 个）：\n\n{problems_text}"
        ),
    ]

    try:
        response = llm.invoke(messages)
        result = json.loads(response.content.strip())
    except (json.JSONDecodeError, Exception):
        result = {
            "summary": "本周问题模式分析",
            "rules": [],
        }

    rules = result.get("rules", [])
    summary = result.get("summary", "")

    # 写入建议文件
    if rules:
        _write_suggestions(week_label, summary, rules, problems)

    return {
        "week_start": _week_start_str(),
        "problem_count": len(problems),
        "summary": summary,
        "rules": rules,
        "file_path": str(SUGGESTIONS_FILE) if rules else None,
    }


def get_suggestions() -> dict:
    """
    读取当前的规则建议文件内容。

    返回:
        dict: {"exists": bool, "content": str|None, "file_path": str}
    """
    if SUGGESTIONS_FILE.exists():
        return {
            "exists": True,
            "content": SUGGESTIONS_FILE.read_text(encoding="utf-8"),
            "file_path": str(SUGGESTIONS_FILE),
        }
    return {
        "exists": False,
        "content": None,
        "file_path": str(SUGGESTIONS_FILE),
    }


# ── 文件写入 ───────────────────────────────────────────────────

def _write_suggestions(week_label: str, summary: str, rules: list, problems: list[dict]):
    """将规则草案写入 rules_suggestions.md（平台无关，适用于 Claude Code/Cursor/Copilot 等）。"""
    # 构建 id → 问题详情的映射
    id_to_problem = {p["id"]: p for p in problems}

    lines = [
        "# 开发规则建议草案",
        "",
        "> 本文件由 DevQuest Rule-Maker 自动生成，适用于所有主流 AI 编程工具。",
        "> 确认的规则可手动合并到：",
        "> - **Claude Code**: `CLAUDE.md` 或 `.claude/rules/*.md`",
        "> - **Cursor**: `.cursorrules`",
        "> - **GitHub Copilot**: `.github/copilot-instructions.md`",
        "",
        f"> 生成时间: {datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC",
        f"> 数据来源: {week_label}，LLM 反思生成",
        f"> 状态: 待人工审核确认",
        "",
        "---",
        "",
        f"## 本周模式摘要",
        "",
        summary,
        "",
        "---",
        "",
    ]

    for i, rule in enumerate(rules, 1):
        conf = rule.get("confidence", 0.5)
        conf_label = "高" if conf >= 0.9 else ("中" if conf >= 0.7 else "低")
        category = rule.get("category", "未分类")
        category_label = {
            "error_handling": "错误处理",
            "code_style": "代码风格",
            "architecture": "架构决策",
            "tooling": "工具/环境",
            "testing": "测试",
        }.get(category, category)

        lines.append(f"### 规则 {i}: {rule.get('rule', '')}")
        lines.append("")
        lines.append(f"- **分类**: {category_label}")
        lines.append(f"- **置信度**: {conf_label} ({conf})")
        lines.append(f"- **理由**: {rule.get('rationale', '无')}")
        lines.append("")

        # 来源问题：带项目名和标题
        source_ids = rule.get("source_problems", [])
        if source_ids:
            lines.append("**来源问题**:")
            for sid in source_ids:
                p = id_to_problem.get(sid)
                if p:
                    proj = p.get("project_name", "")
                    title = p.get("title", "无标题")
                    ptype = p.get("problem_type", "")
                    score = p.get("priority_score", 5)
                    pre = f"[{proj}] " if proj else ""
                    lines.append(f"  - #{sid} {pre}{title} ({ptype}, 评分 {score})")
                else:
                    lines.append(f"  - #{sid}")
            lines.append("")

    lines.extend([
        "---",
        "",
        "## 如何使用",
        "",
        "1. 逐条 review 以上规则，判断是否适合你当前的项目",
        "2. 确认的规则复制到对应工具的规则文件中（见顶部说明）",
        "3. 不合理的规则直接忽略或删除",
        "4. 下次运行反思时会基于新的问题数据重新生成",
    ])

    SUGGESTIONS_FILE.write_text("\n".join(lines), encoding="utf-8")


# ── 工具函数 ───────────────────────────────────────────────────

def _week_start_str() -> str:
    now = datetime.utcnow()
    week_start = now - timedelta(days=now.weekday())
    return week_start.strftime("%Y-%m-%d")


def _week_label() -> str:
    week_start = _week_start_str()
    week_end_dt = datetime.utcnow() - timedelta(days=datetime.utcnow().weekday())
    week_end = (week_end_dt + timedelta(days=6)).strftime("%Y-%m-%d")
    return f"{week_start} ~ {week_end}"
