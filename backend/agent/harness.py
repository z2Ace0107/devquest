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


def _build_push_content(knowledge: dict) -> str:
    """根据当前知识层状态生成推送摘要内容。"""
    parts = [f"本周新增 **{knowledge.get('weekly_new', 0)}** 条技术经验"]
    if knowledge.get("total_topics", 0) > 0:
        parts.append(f"覆盖 **{knowledge['total_topics']}** 个知识主题")
    growing = knowledge.get("growing_topics", [])
    if growing:
        names = ", ".join(t["title"] for t in growing[:5])
        parts.append(f"活跃主题: {names}")
    parts.append("")
    parts.append("> 由 DevQuest Agent 自动生成")
    return "\n".join(parts)


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
        output = state.get("output", {})

        # P1: 未关联 Topic 的孤立 Problem ≥3 → 组织
        if knowledge.get("needs_organize"):
            return {
                "type": "organize",
                "target": [],
                "meta": {
                    "orphan_count": knowledge.get("orphan_count", 0),
                    "reason": "orphan_problems_trigger",
                },
            }

        # P2: Growing Topic 有实质内容 → 编译，若飞书 CLI 可用则推送
        growing = knowledge.get("growing_topics", [])
        if growing:
            t = growing[0]
            cli_available = output.get("feishu_cli_available", False)
            action_type = "compile_push" if cli_available else "compile"
            return {
                "type": action_type,
                "target": [],
                "meta": {
                    "topic_id": t["id"],
                    "topic_name": t["title"],
                    "new_count": t.get("new_count", 0),
                    "push_to_feishu": cli_available,
                    "reason": "growing_topic",
                },
            }

        # P3: 低质量 > 5 条 → 健康检查
        if knowledge.get("low_quality_count", 0) >= 5:
            return {
                "type": "health_check",
                "target": [],
                "meta": {"low_quality": knowledge["low_quality_count"]},
            }

        # P4: 过时 > 10 条 → 健康检查
        if knowledge.get("stale_count", 0) >= 10:
            return {
                "type": "health_check",
                "target": [],
                "meta": {"stale_count": knowledge["stale_count"]},
            }

        # P5: 本周有新经验 + 飞书配置了 → 推送摘要
        if knowledge.get("weekly_new", 0) > 0 and output.get("webhook_ready"):
            return {
                "type": "push",
                "target": [],
                "meta": {
                    "title": "DevQuest 本周经验摘要",
                    "content": _build_push_content(knowledge),
                    "topic_count": max(knowledge.get("total_topics", 0), 1),
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

        if action_type in ("compile", "compile_push"):
            topic_id = meta.get("topic_id")
            topic_name = meta.get("topic_name", "未命名主题")
            push_to_feishu = meta.get("push_to_feishu", False)
            return _tools.compile_tool(topic_id=topic_id, topic_name=topic_name, push_to_feishu=push_to_feishu)

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
    o = state.get("output", {})
    return {
        "total_problems": k.get("total_problems", 0),
        "weekly_new": k.get("weekly_new", 0),
        "needs_organize": k.get("needs_organize", False),
        "stale": k.get("stale_count", 0),
        "low_quality": k.get("low_quality_count", 0),
        "orphan_count": k.get("orphan_count", 0),
        "total_topics": k.get("total_topics", 0),
        "growing_topics_count": len(k.get("growing_topics", [])),
        "webhook_ready": o.get("webhook_ready", False),
        "feishu_cli_available": o.get("feishu_cli_available", False),
    }
