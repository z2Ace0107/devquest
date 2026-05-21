# -*- coding: utf-8 -*-
"""Agent 记忆系统 — 工作记忆 + 短期操作日志"""

import json
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# 当前唤醒周期的工作记忆（内存中）
_working_memory: dict = {}


def remember(action_type: str, targets: list, result: dict):
    """记录一次操作。写入短期日志 + 更新工作记忆。"""
    entry = {
        "action": action_type,
        "targets": targets,
        "result": result,
        "at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
    }
    _working_memory.setdefault("actions", []).append(entry)
    logger.info("Agent action: %s → %s", action_type, result.get("summary", "done"))


def recall(actions_since: int = 10) -> list[dict]:
    """回溯最近 N 次操作。"""
    actions = _working_memory.get("actions", [])
    return actions[-actions_since:] if actions else []


def last_action() -> dict | None:
    """最近一次操作。"""
    actions = _working_memory.get("actions", [])
    return actions[-1] if actions else None


def reset_working_memory():
    """重置工作记忆（Agent 休眠时调用）。"""
    global _working_memory
    _working_memory = {}
