# -*- coding: utf-8 -*-
"""Agent 记忆系统 — 工作记忆 + 短期操作日志（AgentAction 持久化）"""

import json
import logging
from datetime import datetime, timezone

from backend.database import SessionLocal
from backend.models import AgentAction

logger = logging.getLogger(__name__)

# 当前唤醒周期的工作记忆（内存中）
_working_memory: dict = {}


def remember(action_type: str, targets: list, result: dict):
    """记录一次操作。写入 AgentAction 表 + 更新工作记忆。"""
    entry = {
        "action": action_type,
        "targets": targets,
        "result": result,
        "at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
    }
    _working_memory.setdefault("actions", []).append(entry)

    # 持久化到 AgentAction 表
    db = SessionLocal()
    try:
        record = AgentAction(
            action_type=action_type,
            target_ids=json.dumps(targets) if targets else None,
            result=json.dumps(result, ensure_ascii=False) if result else None,
        )
        db.add(record)
        db.commit()
    except Exception:
        logger.exception("AgentAction 持久化失败")
    finally:
        db.close()

    logger.info("Agent action: %s → %s", action_type, result.get("summary", "done"))


def recall(actions_since: int = 10) -> list[dict]:
    """回溯最近 N 次操作（先从 DB 加载，再合并工作内存）。"""
    db = SessionLocal()
    try:
        rows = db.query(AgentAction).order_by(
            AgentAction.created_at.desc()
        ).limit(actions_since).all()
        return [
            {
                "id": r.id,
                "action": r.action_type,
                "targets": json.loads(r.target_ids) if r.target_ids else [],
                "result": json.loads(r.result) if r.result else {},
                "at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in reversed(rows)
        ]
    except Exception:
        logger.exception("AgentAction 查询失败")
        return []
    finally:
        db.close()


def last_action() -> dict | None:
    """最近一次操作。"""
    actions = recall(1)
    return actions[0] if actions else None


def reset_working_memory():
    """重置工作记忆（Agent 休眠时调用）。"""
    global _working_memory
    _working_memory = {}
