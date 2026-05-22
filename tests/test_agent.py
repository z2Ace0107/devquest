"""测试 Agent 框架 — harness/state/tools/memory/guardrails + V4.0 数据模型"""

import json
import pytest
from backend.agent import state as _state
from backend.agent import memory as _memory
from backend.agent import guardrails as _guardrails
from backend.agent.harness import HarnessAgent, _build_push_content
from backend.database import SessionLocal, init_db
from backend.models import Problem, Topic, Concept, Link, AgentAction


# ── 数据模型测试 ──────────────────────────────────────

def test_topic_creation():
    """Topic 模型可通过 SQLAlchemy 创建并查询。"""
    init_db()
    db = SessionLocal()
    try:
        t = Topic(title="Test Topic", summary="测试摘要", problem_count=3)
        db.add(t)
        db.commit()
        assert t.id is not None
        assert t.solution_status == "需跟进"
        assert t.freshness_score == 1.0
        # cleanup
        db.delete(t)
        db.commit()
    finally:
        db.close()


def test_concept_creation():
    """Concept 模型创建和别名 JSON。"""
    init_db()
    db = SessionLocal()
    try:
        c = Concept(name="Docker", type="工具",
                    aliases=json.dumps(["docker", "容器"]))
        db.add(c)
        db.commit()
        assert c.id is not None
        assert c.type == "工具"
        # cleanup
        db.delete(c)
        db.commit()
    finally:
        db.close()


def test_link_creation():
    """Link 模型连接 Problem → Topic。"""
    init_db()
    db = SessionLocal()
    try:
        t = Topic(title="LinkTest")
        db.add(t)
        db.flush()
        link = Link(source_type="Problem", source_id=1,
                    target_type="Topic", target_id=t.id,
                    relation_type="属于")
        db.add(link)
        db.commit()
        assert link.id is not None

        found = db.query(Link).filter(
            Link.source_type == "Problem",
            Link.target_type == "Topic",
            Link.relation_type == "属于",
        ).first()
        assert found is not None

        db.delete(link)
        db.delete(t)
        db.commit()
    finally:
        db.close()


def test_agent_action_persistence():
    """AgentAction 持久化和查询。"""
    init_db()
    db = SessionLocal()
    try:
        a = AgentAction(action_type="test", target_ids="[1,2]",
                        result=json.dumps({"ok": True}))
        db.add(a)
        db.commit()
        assert a.id is not None

        found = db.query(AgentAction).filter(
            AgentAction.action_type == "test"
        ).first()
        assert found is not None
        assert json.loads(found.result) == {"ok": True}

        db.delete(a)
        db.commit()
    finally:
        db.close()


# ── State 层测试 ─────────────────────────────────────

def test_observe_returns_structure():
    s = _state.observe()
    assert "knowledge" in s
    assert "output" in s
    assert "input" in s
    assert "total_problems" in s["knowledge"]
    assert "total_topics" in s["knowledge"]
    assert "orphan_count" in s["knowledge"]
    assert "growing_topics" in s["knowledge"]


# ── Memory 层测试 ────────────────────────────────────

def test_memory_remember_and_recall():
    _memory.reset_working_memory()
    _memory.remember("organize", [1, 2], {"ok": True, "summary": "done"})
    actions = _memory.recall(10)
    assert len(actions) >= 1
    # 工作内存
    _memory.reset_working_memory()


def test_memory_last_action():
    _memory.reset_working_memory()
    _memory.remember("push", [], {"ok": True})
    last = _memory.last_action()
    assert last is not None
    assert last["action"] == "push"
    _memory.reset_working_memory()


# ── Guardrails 测试 ─────────────────────────────────

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


# ── Tools 测试 ──────────────────────────────────────

def test_organize_tool():
    from backend.agent.tools import organize_tool
    result = organize_tool([])
    assert "total_problems" in result
    assert "groups" in result
    assert "topics_created" in result


def test_compile_tool_no_args_errors():
    from backend.agent.tools import compile_tool
    result = compile_tool()
    assert "error" in result


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


# ── Harness Agent 集成测试 ──────────────────────────

def test_harness_agent_runs():
    agent = HarnessAgent()
    result = agent.run()
    assert "actions" in result
    assert "state" in result


def test_build_push_content():
    knowledge = {
        "weekly_new": 10,
        "total_topics": 5,
        "growing_topics": [{"id": 1, "title": "Docker", "new_count": 3}],
    }
    content = _build_push_content(knowledge)
    assert "10" in content
    assert "5" in content
    assert "Docker" in content
    assert len(content) >= 50
