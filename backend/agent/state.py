# -*- coding: utf-8 -*-
"""Agent 状态感知 — 读取知识层、输出层、输入层状态"""

import json
import logging
from datetime import datetime, timezone, timedelta

from backend.database import SessionLocal
from backend.models import Problem

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

    # 最近一周新增
    weekly_new = db.query(Problem).filter(Problem.created_at >= week_ago).count()

    # 最近一小时新增
    recent = db.query(Problem).filter(Problem.created_at >= hour_ago).count()

    # 长期未更新的问题（可能过时）
    stale = db.query(Problem).filter(
        Problem.created_at < month_ago,
        Problem.usage_count == 0
    ).count()

    # 低质量经验
    low_quality = db.query(Problem).filter(
        Problem.feedback_count > 2,
        Problem.feedback_score < 0.3
    ).count()

    # 无项目归属的
    orphan_count = 0  # V4.0 后续升级（Topic 关联后判断）

    return {
        "total_problems": total,
        "weekly_new": weekly_new,
        "recent_captures": recent,
        "stale_count": stale,
        "low_quality_count": low_quality,
        "orphan_count": orphan_count,
        "needs_organize": recent >= 3,  # 3 条以上触发组织
    }


def _observe_output(db) -> dict:
    """输出层状态：飞书同步和推送情况。"""
    # V4.0 MVP: 从 env 读取 webhook 状态
    import os
    webhook_configured = bool(os.getenv("FEISHU_WEBHOOK_URL", "").startswith("https://"))

    # 读推送历史（从 AgentAction 表，V4.0 初版可用现有 feishu.py 日志）
    return {
        "webhook_ready": webhook_configured,
        "feishu_cli_available": False,  # V4.1 升级
        "pending_pushes": 0,  # V4.0 暂不追踪
        "last_push_at": None,
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
