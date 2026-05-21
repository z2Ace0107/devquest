# -*- coding: utf-8 -*-
"""Agent 工具集 — Agent 通过这 8 个工具操作系统"""

import json
import logging
import os
from datetime import datetime, timezone, timedelta

from backend.database import SessionLocal
from backend.models import Problem
from backend import services, vector_search

logger = logging.getLogger(__name__)


# ── observe ──────────────────────────────────────────────

def observe_tool() -> dict:
    """读取全局状态。"""
    from backend.agent.state import observe as _observe
    return _observe()


# ── capture ─────────────────────────────────────────────

def capture_tool(conversation_text: str, project: str = None) -> dict:
    """从对话文本提取经验入库。复用 save_problem 或将原始文本 LLM 提取。"""
    # 短文本直接当 error 录
    if len(conversation_text.strip()) < 800:
        return services.save_problem_service(
            error=conversation_text.strip(),
            solution="（待补充）",
            project=project or "DevQuest",
        )
    # 长文本走 extractor LLM 流水线
    from backend import extractor as _extractor
    result = _extractor.extract_problems(conversation_text, project_name=project or "DevQuest")
    return {"count": len(result), "problem_ids": [p["id"] for p in result]}


# ── organize ─────────────────────────────────────────────

def organize_tool(problem_ids: list[int] = None) -> dict:
    """聚类未归类 Problem。V4.0 MVP: 返回需要组织的信息给 Agent。

    后续 V4.0 完整版会调用 Topic 聚类逻辑。当前返回待组织摘要。"""
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        week_ago = now - timedelta(days=7)

        if problem_ids:
            problems = db.query(Problem).filter(Problem.id.in_(problem_ids)).all()
        else:
            problems = db.query(Problem).filter(Problem.created_at >= week_ago).all()

        # 按 tech_stack 简单分组
        groups = {}
        for p in problems:
            tech = (p.tech_stack or "未分类").split(",")[0].strip()
            groups.setdefault(tech, []).append(p.id)

        return {
            "total": len(problems),
            "groups": {k: len(v) for k, v in groups.items()},
            "suggested_topics": [k for k, v in groups.items() if len(v) >= 2],
            "summary": f"共 {len(problems)} 条待组织，可归为 {len(groups)} 个技术分组",
        }
    finally:
        db.close()


# ── compile ─────────────────────────────────────────────

def compile_tool(topic_name: str, problem_ids: list[int] = None) -> dict:
    """编译 Topic 内容为飞书文档 Markdown。

    参数:
        topic_name: 主题名（如 "Docker环境配置"）
        problem_ids: 关联的 Problem ID 列表
    """
    db = SessionLocal()
    try:
        pids = problem_ids or []
        problems = db.query(Problem).filter(Problem.id.in_(pids)).all() if pids else []

        env_summary = _extract_env_summary(problems)
        solution_versions = max((p.solution_version or 1 for p in problems), default=1)
        first_seen = min((p.first_seen_at or p.created_at for p in problems), default=None)
        last_seen = max((p.created_at for p in problems), default=None)

        lines = [
            f"## {topic_name}",
            "",
            f"> 共 {len(problems)} 条经验 · 方案迭代至 v{solution_versions}",
        ]
        if env_summary:
            lines.append(f"> 已验证环境: {env_summary}")
        if first_seen:
            lines.append(f"> 首次出现: {first_seen.strftime('%Y-%m-%d')}")
        if last_seen:
            lines.append(f"> 最近更新: {last_seen.strftime('%Y-%m-%d')}")
        lines.append("")

        for p in problems[:10]:
            lines.append(f"### {p.title or '未命名'}")
            lines.append(f"- **方案** (v{p.solution_version or 1}): {p.solution or '无'}")
            lines.append(f"- **评分**: {p.priority_score or 5}/10 · **反馈**: {p.feedback_score or 0:.0%}")
            lines.append("")

        content = "\n".join(lines)
        return {
            "topic_name": topic_name,
            "problem_count": len(problems),
            "content": content,
            "content_length": len(content),
        }
    finally:
        db.close()


def _extract_env_summary(problems: list) -> str:
    """从 Problem 列表中提取环境摘要。"""
    oss = set()
    for p in problems:
        env_str = p.environment
        if env_str:
            try:
                env = json.loads(env_str) if isinstance(env_str, str) else env_str
                os_val = env.get("os") if isinstance(env, dict) else None
                if os_val:
                    oss.add(os_val)
            except (json.JSONDecodeError, TypeError):
                pass
    return ", ".join(sorted(oss)) if oss else ""


# ── search ───────────────────────────────────────────────

def search_tool(query: str, k: int = 5) -> dict:
    """多通道 RRF 检索。"""
    return vector_search.search(query_text=query, k=k)


# ── health_check ─────────────────────────────────────────

def health_check_tool() -> dict:
    """扫描经验库问题：矛盾、过时、低质。"""
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        month_ago = now - timedelta(days=30)

        issues = []
        # 低质量
        low_q = db.query(Problem).filter(
            Problem.feedback_count >= 3,
            Problem.feedback_score < 0.3
        ).count()
        if low_q:
            issues.append({"type": "low_quality", "count": low_q, "action": "review"})

        # 过时
        stale = db.query(Problem).filter(
            Problem.created_at < month_ago,
            Problem.usage_count == 0
        ).count()
        if stale:
            issues.append({"type": "stale", "count": stale, "action": "archive_or_mark"})

        # 未解决（solution 为空或"未解决"）
        unsolved = db.query(Problem).filter(
            (Problem.solution == None) | (Problem.solution == "") | (Problem.solution == "未解决")
        ).count()
        if unsolved:
            issues.append({"type": "unsolved", "count": unsolved, "action": "follow_up"})

        return {"healthy": len(issues) == 0, "issues": issues, "summary": f"发现 {len(issues)} 类问题"}
    finally:
        db.close()


# ── feishu_status ────────────────────────────────────────

def feishu_status_tool() -> dict:
    """读取飞书输出层状态。"""
    webhook = os.getenv("FEISHU_WEBHOOK_URL", "")
    return {
        "webhook_ready": webhook.startswith("https://"),
        "feishu_cli_available": False,  # V4.1
        "docs_synced": 0,
        "summary": "飞书 Webhook 已配置" if webhook.startswith("https://") else "飞书未配置",
    }


# ── push ─────────────────────────────────────────────────

def push_tool(title: str, content: str, template: str = "blue") -> dict:
    """推送消息卡片到飞书。"""
    webhook = os.getenv("FEISHU_WEBHOOK_URL", "")
    if not webhook.startswith("https://"):
        return {"ok": False, "error": "FEISHU_WEBHOOK_URL 未配置"}

    from backend import feishu as _feishu
    return _feishu.send_card(webhook, title, content, template)
