# -*- coding: utf-8 -*-
"""Agent Guardrails — 执行前质量约束检查"""

import logging

logger = logging.getLogger(__name__)


def evaluate(action: dict, state: dict) -> tuple[str, str]:
    """评估一个待执行的 Action 是否应该通过。

    参数:
        action: {"type": str, "target": list, "meta": dict}
        state: observe() 返回的全局状态

    返回:
        ("pass", reason) / ("warn", reason) / ("block", reason)
    """
    action_type = action.get("type", "")
    checker = _RULES.get(action_type)
    if checker:
        return checker(action, state)
    return ("pass", "")


# ── 各 Action 类型的约束规则 ──────────────────────────

def _check_push(action: dict, state: dict) -> tuple[str, str]:
    """推送约束：防止刷屏 + 防止推送空内容。"""
    knowledge = state.get("knowledge", {})
    content_md = action.get("meta", {}).get("content", "")
    topic_count = action.get("meta", {}).get("topic_count", 0)

    if not content_md or len(content_md.strip()) < 50:
        return ("block", "推送内容过短 (<50 字)")
    if topic_count < 1 and state["input"].get("recent_captures", 0) == 0:
        return ("block", "无有效内容可推送")

    return ("pass", "")


def _check_compile(action: dict, state: dict) -> tuple[str, str]:
    """编译约束：经验不足不编译飞书文档。"""
    min_problems = action.get("meta", {}).get("problem_count", 0)
    if min_problems < 2:
        return ("block", f"Topic 下仅 {min_problems} 条经验，至少需要 2 条")
    return ("pass", "")


def _check_organize(action: dict, state: dict) -> tuple[str, str]:
    """组织约束：有未归类才组织。"""
    knowledge = state.get("knowledge", {})
    needs = knowledge.get("needs_organize", False)
    if not needs:
        return ("warn", "当前无需组织（非紧急）")
    return ("pass", "")


_RULES = {
    "push": _check_push,
    "compile": _check_compile,
    "organize": _check_organize,
}
