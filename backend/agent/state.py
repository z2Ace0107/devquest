# -*- coding: utf-8 -*-
"""Agent 状态感知 — 读取知识层、输出层、输入层状态"""

import json
import logging
from datetime import datetime, timezone, timedelta

from backend.database import SessionLocal
from backend.models import Problem, Topic, Link, AgentAction

logger = logging.getLogger(__name__)


def observe() -> dict:
    """读取全局状态，返回三个维度的快照。

    返回结构:
    {
        "knowledge": {...},   # 知识层
        "output": {...},      # 输出层（飞书）
        "input": {...}        # 输入层
    }
    """
    db = SessionLocal()
    try:
        return {
            "knowledge": _observe_knowledge(db),
            "output": _observe_output(db),
            "input": _observe_input(db),
        }
    finally:
        db.close()


def _observe_knowledge(db) -> dict:
    """知识层状态：Problem/Topic 的健康和活跃度。"""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    week_ago = now - timedelta(days=7)
    month_ago = now - timedelta(days=30)
    hour_ago = now - timedelta(hours=1)

    total = db.query(Problem).count()
    weekly_new = db.query(Problem).filter(Problem.created_at >= week_ago).count()
    recent = db.query(Problem).filter(Problem.created_at >= hour_ago).count()
    stale = db.query(Problem).filter(
        Problem.created_at < month_ago,
        Problem.usage_count == 0
    ).count()
    low_quality = db.query(Problem).filter(
        Problem.feedback_count > 2,
        Problem.feedback_score < 0.3
    ).count()

    # Topic 状态
    total_topics = db.query(Topic).count()
    growing_topics = []
    stale_topics = []
    all_topics = db.query(Topic).all()
    for t in all_topics:
        # Growing: 最近 7 天有 ≥3 条新 Problem 关联到此 Topic
        recent_link_count = db.query(Link).filter(
            Link.target_type == "Topic",
            Link.target_id == t.id,
            Link.relation_type == "属于",
            Link.created_at >= week_ago,
        ).count()
        if recent_link_count >= 3:
            growing_topics.append({"id": t.id, "title": t.title, "new_count": recent_link_count})
        # Stale: >30 天未更新且 solution_status != '已解决'
        if t.updated_at and t.updated_at < month_ago and t.solution_status != "已解决":
            stale_topics.append({"id": t.id, "title": t.title, "updated_at": t.updated_at.isoformat()})

    # 孤立 Problem（未关联任何 Topic）
    all_problem_ids = {p[0] for p in db.query(Problem.id).all()}
    linked_problem_ids = set()
    for row in db.query(Link.source_id).filter(
        Link.source_type == "Problem",
        Link.target_type == "Topic",
        Link.relation_type == "属于",
    ).all():
        linked_problem_ids.add(row[0])
    orphan_count = len(all_problem_ids - linked_problem_ids)

    # 最近 Agent 运行
    last_action = db.query(AgentAction).order_by(AgentAction.created_at.desc()).first()
    last_run = last_action.created_at.isoformat() if last_action else None

    return {
        "total_problems": total,
        "weekly_new": weekly_new,
        "recent_captures": recent,
        "stale_count": stale,
        "low_quality_count": low_quality,
        "orphan_count": orphan_count,
        "needs_organize": orphan_count >= 3,
        "total_topics": total_topics,
        "growing_topics": growing_topics,
        "stale_topics": stale_topics,
        "last_agent_run": last_run,
    }


def _observe_output(db) -> dict:
    """输出层状态：飞书同步和推送情况。"""
    import os
    from backend import feishu_cli

    webhook_configured = bool(os.getenv("FEISHU_WEBHOOK_URL", "").startswith("https://"))
    feishu_cli_available = feishu_cli.is_available()

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    day_ago = now - timedelta(days=1)

    # 有变更但 24h 内未推送的 Topic
    pending_topics = db.query(Topic).filter(
        Topic.updated_at >= day_ago,
        Topic.feishu_doc_id == None,
    ).count()

    # 已有飞书文档的 Topic 数
    synced_topics = db.query(Topic).filter(
        Topic.feishu_doc_id != None,
    ).count()

    # 最近推送
    last_push = db.query(AgentAction).filter(
        AgentAction.action_type == "push",
    ).order_by(AgentAction.created_at.desc()).first()

    return {
        "webhook_ready": webhook_configured,
        "feishu_cli_available": feishu_cli_available,
        "docs_synced": synced_topics,
        "pending_pushes": pending_topics,
        "last_push_at": last_push.created_at.isoformat() if last_push else None,
    }


def _observe_input(db) -> dict:
    """输入层状态：最近的采集活动。"""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    hour_ago = now - timedelta(hours=1)

    recent = db.query(Problem).filter(Problem.created_at >= hour_ago).count()

    return {
        "recent_captures": recent,
        "hook_active": False,  # V4.2 升级
        "last_manual_save": None,
    }
