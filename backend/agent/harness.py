# -*- coding: utf-8 -*-
"""
DevQuest Harness Agent — 单 Agent 全局大脑

执行循环: observe → plan → evaluate → execute → remember

Agent 不硬编码阈值，通过观察全局状态自主判断下一步该做什么。
"""

import logging

from backend.agent import state as _state
from backend.agent import memory as _memory
from backend.agent import guardrails as _guardrails
from backend.agent import tools as _tools

logger = logging.getLogger(__name__)


class HarnessAgent:
    """单 Agent Harness。每次唤醒执行一次认知循环。"""

    def run(self) -> dict:
        """执行一次完整的 Agent 循环。返回本次操作摘要。"""
        results = {"actions": [], "state": None}

        # 1. 观察
        current_state = _state.observe()
        results["state"] = _summarize_state(current_state)

        # 2. 规划（当前版本：规则引擎；V4.0 完整版：Claude 推理）
        action = self._plan(current_state)

        if action is None:
            logger.info("Agent: 无操作需要执行")
            results["decision"] = "idle"
            return results

        results["decision"] = action["type"]

        # 3. 评估
        verdict, reason = _guardrails.evaluate(action, current_state)
        if verdict == "block":
            logger.warning("Agent: %s 被阻止 — %s", action["type"], reason)
            results["action_result"] = {"blocked": True, "reason": reason}
            return results
        if verdict == "warn":
            logger.info("Agent: %s 有警告 — %s", action["type"], reason)

        # 4. 执行
        try:
            result = self._execute(action)
        except Exception as e:
            logger.exception("Agent: %s 执行失败", action["type"])
            result = {"error": str(e)}

        # 5. 记忆
        _memory.remember(
            action["type"],
            action.get("target", []),
            result,
        )

        results["actions"].append({"type": action["type"], "result": result})
        results["action_result"] = result
        return results

    # ── 规划引擎 ────────────────────────────────────────

    def _plan(self, state: dict) -> dict | None:
        """基于当前状态决定下一步动作。

        优先级: 组织(高价值) > 健康检查(维护) > 推送(有内容才推)
        """
        knowledge = state.get("knowledge", {})

        # P1: 有新经验 → 先组织
        if knowledge.get("needs_organize"):
            return {
                "type": "organize",
                "target": [],
                "meta": {
                    "weekly_new": knowledge.get("weekly_new", 0),
                    "reason": "recent_captures_trigger",
                },
            }

        # P2: 低质量 > 5 条 → 提醒健康检查
        if knowledge.get("low_quality_count", 0) >= 5:
            return {
                "type": "health_check",
                "target": [],
                "meta": {"low_quality": knowledge["low_quality_count"]},
            }

        # P3: 过时 > 10 条 → 提醒清理
        if knowledge.get("stale_count", 0) >= 10:
            return {
                "type": "health_check",
                "target": [],
                "meta": {"stale_count": knowledge["stale_count"]},
            }

        # P4: 本周有新经验 + 飞书配置了 → 推送周报
        if (knowledge.get("weekly_new", 0) > 0
                and state.get("output", {}).get("webhook_ready")):
            return {
                "type": "push",
                "target": [],
                "meta": {
                    "content": f"本周新增 {knowledge['weekly_new']} 条经验",
                    "topic_count": knowledge["weekly_new"],
                    "reason": "weekly_summary",
                },
            }

        return None

    # ── 执行引擎 ────────────────────────────────────────

    def _execute(self, action: dict) -> dict:
        """根据 action type 调用对应工具。"""
        action_type = action["type"]
        meta = action.get("meta", {})
        target = action.get("target", [])

        if action_type == "organize":
            return _tools.organize_tool(target if target else None)

        if action_type == "health_check":
            return _tools.health_check_tool()

        if action_type == "push":
            title = meta.get("title", "📊 DevQuest 经验摘要")
            content = meta.get("content", "")
            return _tools.push_tool(title, content)

        if action_type == "compile":
            topic = meta.get("topic_name", "未命名主题")
            return _tools.compile_tool(topic, target)

        if action_type == "capture":
            text = meta.get("conversation_text", "")
            project = meta.get("project")
            return _tools.capture_tool(text, project)

        if action_type == "search":
            query = meta.get("query", "")
            k = meta.get("k", 5)
            return _tools.search_tool(query, k)

        if action_type == "feishu_status":
            return _tools.feishu_status_tool()

        return {"error": f"未知 action_type: {action_type}"}


# ── 辅助 ────────────────────────────────────────────────

def _summarize_state(state: dict) -> dict:
    """压缩状态为可读摘要。"""
    k = state.get("knowledge", {})
    return {
        "total_problems": k.get("total_problems", 0),
        "weekly_new": k.get("weekly_new", 0),
        "needs_organize": k.get("needs_organize", False),
        "stale": k.get("stale_count", 0),
        "low_quality": k.get("low_quality_count", 0),
        "webhook_ready": state.get("output", {}).get("webhook_ready", False),
    }
