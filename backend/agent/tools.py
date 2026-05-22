# -*- coding: utf-8 -*-
"""Agent 工具集 — Agent 通过这 8 个工具操作系统"""

import json
import logging
import os
from datetime import datetime, timezone, timedelta

from backend.database import SessionLocal
from backend.models import Problem, Topic, Concept, Link, AgentAction
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
    """聚类 Problem → Topic，创建/更新 Topic 记录 + Problem-Topic Link。

    分组策略: 按 tech_stack 首项分组，>=2 条经验创建一个 Topic。
    """
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc).replace(tzinfo=None)

        if problem_ids:
            problems = db.query(Problem).filter(Problem.id.in_(problem_ids)).all()
        else:
            problems = db.query(Problem).all()

        # 按 tech_stack 第一项分组
        groups: dict[str, list[Problem]] = {}
        for p in problems:
            tech = (p.tech_stack or "未分类").split(",")[0].strip()
            groups.setdefault(tech, []).append(p)

        created_topics = []
        updated_topics = []

        for tech_name, group in groups.items():
            if len(group) < 2:
                continue

            # 查找或创建 Topic
            topic = db.query(Topic).filter(Topic.title == tech_name).first()
            if topic is None:
                topic = Topic(
                    title=tech_name,
                    first_seen_at=min(p.first_seen_at or p.created_at for p in group),
                    problem_count=len(group),
                    project_count=len(set(p.project_id for p in group)),
                    summary=_gen_topic_summary(group),
                    freshness_score=1.0,
                    solution_status="需跟进",
                )
                db.add(topic)
                db.flush()
                created_topics.append({"id": topic.id, "title": topic.title, "problem_count": len(group)})
            else:
                topic.problem_count = db.query(Problem).filter(
                    Problem.tech_stack.like(f"{tech_name}%")
                ).count()
                pids = db.query(Problem.project_id).filter(
                    Problem.tech_stack.like(f"{tech_name}%")
                ).all()
                topic.project_count = len(set(r[0] for r in pids))
                topic.summary = _gen_topic_summary(group)
                topic.updated_at = now
                topic.freshness_score = min(1.0, topic.freshness_score + 0.1)
                updated_topics.append({"id": topic.id, "title": topic.title, "problem_count": topic.problem_count})

            # 创建 Problem → Topic link（不重复）
            for p in group:
                exists = db.query(Link).filter(
                    Link.source_type == "Problem",
                    Link.source_id == p.id,
                    Link.target_type == "Topic",
                    Link.target_id == topic.id,
                    Link.relation_type == "属于",
                ).first()
                if not exists:
                    db.add(Link(
                        source_type="Problem", source_id=p.id,
                        target_type="Topic", target_id=topic.id,
                        relation_type="属于",
                    ))

        db.commit()

        return {
            "total_problems": len(problems),
            "groups": {k: len(v) for k, v in groups.items()},
            "topics_created": created_topics,
            "topics_updated": updated_topics,
            "topic_count": len(created_topics) + len(updated_topics),
            "summary": (f"创建 {len(created_topics)} 个新主题, "
                        f"更新 {len(updated_topics)} 个已有主题"),
        }
    finally:
        db.close()


def _gen_topic_summary(problems: list[Problem]) -> str:
    """从 Problem 列表生成简短 Topic 摘要。"""
    titles = [p.title for p in problems if p.title]
    types = [p.problem_type for p in problems if p.problem_type]
    type_counts = {}
    for t in types:
        type_counts[t] = type_counts.get(t, 0) + 1
    type_str = "、".join(f"{k}({v})" for k, v in sorted(type_counts.items(), key=lambda x: -x[1])[:3])
    return (f"涵盖 {type_str}等 {len(problems)} 条经验。"
            f"典型问题: {'; '.join(titles[:3])}")


# ── compile ─────────────────────────────────────────────

def compile_tool(topic_id: int = None, topic_name: str = None, problem_ids: list[int] = None,
                 push_to_feishu: bool = False) -> dict:
    """编译 Topic → 飞书文档 Markdown，可选推送到飞书 Doc。

    优先用 topic_id 从 Topic 表查找并通过 Link 表找关联 Problem；
    兼容旧接口 topic_name + problem_ids。
    """
    from backend import feishu_cli

    db = SessionLocal()
    try:
        topic = None
        problems = []

        if topic_id:
            topic = db.query(Topic).filter(Topic.id == topic_id).first()
            if topic is None:
                return {"error": f"Topic #{topic_id} 不存在"}

            # 通过 Link 表查找关联的 Problem
            link_rows = db.query(Link).filter(
                Link.target_type == "Topic",
                Link.target_id == topic_id,
                Link.relation_type == "属于",
            ).all()
            pids = [l.source_id for l in link_rows]
            if pids:
                problems = db.query(Problem).filter(Problem.id.in_(pids)).all()
            topic_name = topic.title
        elif problem_ids:
            problems = db.query(Problem).filter(Problem.id.in_(problem_ids)).all()
            topic_name = topic_name or "未命名主题"
            topic = db.query(Topic).filter(Topic.title == topic_name).first()
        else:
            return {"error": "需要提供 topic_id 或 problem_ids"}

        if not problems:
            return {"error": f"Topic '{topic_name}' 下无关联经验", "problem_count": 0}

        # 编译内容
        lines = [
            f"## {topic_name}",
            "",
        ]
        if topic and topic.summary:
            lines.append(f"> {topic.summary}")
            lines.append("")

        env_summary = _extract_env_summary(problems)
        lines.append(f"**经验数**: {len(problems)} · **方案迭代**: v{max((p.solution_version or 1 for p in problems), default=1)}")
        if env_summary:
            lines.append(f"**已验证环境**: {env_summary}")
        first_seen = min((p.first_seen_at or p.created_at for p in problems), default=None)
        last_seen = max((p.created_at for p in problems), default=None)
        if first_seen:
            lines.append(f"**时间跨度**: {first_seen.strftime('%Y-%m-%d')} ~ {last_seen.strftime('%Y-%m-%d')}" if last_seen else "")
        lines.append("")
        lines.append("---")
        lines.append("")

        for i, p in enumerate(problems[:20], 1):
            lines.append(f"### {i}. {p.title or '未命名'}")
            lines.append(f"- **类型**: {p.problem_type or '未知'}")
            lines.append(f"- **方案** (v{p.solution_version or 1}): {p.solution or '无'}")
            lines.append(f"- **评分**: {p.priority_score or 5}/10 · **有用率**: {p.feedback_score:.0%}" if p.feedback_score else f"- **评分**: {p.priority_score or 5}/10")
            lines.append("")

        content = "\n".join(lines)
        result = {
            "topic_name": topic_name,
            "topic_id": topic.id if topic else None,
            "problem_count": len(problems),
            "content": content,
            "content_length": len(content),
        }
        if topic:
            result["feishu_doc_id"] = topic.feishu_doc_id
            result["solution_status"] = topic.solution_status

        # 推送到飞书文档
        if push_to_feishu:
            client = feishu_cli.get_client()
            if client and client.available:
                if topic and topic.feishu_doc_id:
                    doc_result = client.update_doc(topic.feishu_doc_id, topic_name, content)
                else:
                    doc_result = client.create_doc(topic_name, content)
                result["feishu_push"] = doc_result
                if doc_result.get("doc_id") and topic:
                    topic.feishu_doc_id = doc_result["doc_id"]
                    db.commit()
            else:
                result["feishu_push"] = {"error": "飞书 App ID / App Secret 未配置"}

        return result
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
    from backend import feishu_cli

    webhook = os.getenv("FEISHU_WEBHOOK_URL", "")
    cli_available = feishu_cli.FeishuClient.is_configured()

    synced_docs = 0
    if cli_available:
        db = SessionLocal()
        try:
            synced_docs = db.query(Topic).filter(Topic.feishu_doc_id != None).count()
        finally:
            db.close()

    parts = []
    if webhook.startswith("https://"):
        parts.append("Webhook 已配置")
    if cli_available:
        parts.append(f"Open API 已配置 ({synced_docs} 篇文档已同步)")
    summary = "、".join(parts) if parts else "飞书未配置"

    return {
        "webhook_ready": webhook.startswith("https://"),
        "feishu_cli_available": cli_available,
        "docs_synced": synced_docs,
        "summary": summary,
    }


# ── push ─────────────────────────────────────────────────

def push_tool(title: str, content: str, template: str = "blue") -> dict:
    """推送消息卡片到飞书。"""
    webhook = os.getenv("FEISHU_WEBHOOK_URL", "")
    if not webhook.startswith("https://"):
        return {"ok": False, "error": "FEISHU_WEBHOOK_URL 未配置"}

    from backend import feishu as _feishu
    return _feishu.send_card(webhook, title, content, template)
