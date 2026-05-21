"""测试 Agent 框架 — harness/state/tools/memory/guardrails"""

import pytest
from backend.agent import state as _state
from backend.agent import memory as _memory
from backend.agent import guardrails as _guardrails
from backend.agent.harness import HarnessAgent


def test_observe_returns_structure():
    s = _state.observe()
    assert "knowledge" in s
    assert "output" in s
    assert "input" in s
    assert "total_problems" in s["knowledge"]


def test_memory_remember_and_recall():
    _memory.reset_working_memory()
    _memory.remember("organize", [1, 2], {"ok": True, "summary": "done"})
    actions = _memory.recall(10)
    assert len(actions) == 1
    assert actions[0]["action"] == "organize"


def test_guardrails_block_empty_push():
    action = {"type": "push", "target": [], "meta": {"content": "", "topic_count": 0}}
    state = {"knowledge": {"needs_organize": False}, "input": {"recent_captures": 0}}
    verdict, _ = _guardrails.evaluate(action, state)
    assert verdict == "block"


def test_guardrails_block_few_problems():
    action = {"type": "compile", "target": [], "meta": {"problem_count": 1}}
    verdict, _ = _guardrails.evaluate(action, {"knowledge": {}})
    assert verdict == "block"


def test_guardrails_pass_valid_push():
    content = "本周新增 5 条经验，包括 Docker 环境配置、Python 包管理、Nginx 反向代理等方向的踩坑记录与解决方案汇总。"
    action = {"type": "push", "target": [], "meta": {"content": content, "topic_count": 3}}
    state = {"knowledge": {}, "input": {"recent_captures": 0}}
    verdict, _ = _guardrails.evaluate(action, state)
    assert verdict == "pass"


def test_organize_tool():
    from backend.agent.tools import organize_tool
    result = organize_tool([])
    assert "total" in result
    assert "groups" in result


def test_health_check_tool():
    from backend.agent.tools import health_check_tool
    result = health_check_tool()
    assert "healthy" in result
    assert "issues" in result


def test_feishu_status_tool():
    from backend.agent.tools import feishu_status_tool
    result = feishu_status_tool()
    assert "webhook_ready" in result


def test_search_tool():
    from backend.agent.tools import search_tool
    result = search_tool("docker", k=3)
    assert "results" in result


def test_harness_agent_runs():
    agent = HarnessAgent()
    result = agent.run()
    assert "actions" in result
    assert "state" in result
