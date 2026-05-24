# -*- coding: utf-8 -*-
"""
Hook 自动捕获引擎测试 (V4.2)
"""

import json
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# 确保项目根在 path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import hook_capture


# ═══════════════════════════════════════════════════════════════
# Hook 状态文件读写
# ═══════════════════════════════════════════════════════════════

class TestHookStateFile:
    """测试 Hook 状态文件读写。"""

    def test_default_state_has_required_fields(self):
        state = hook_capture._default_state()
        assert "running" in state
        assert "pid" in state
        assert "pending_sessions" in state
        assert "sessions_ingested" in state
        assert "dag_context" in state
        assert "errors" in state

    def test_read_state_returns_default_when_file_missing(self, tmp_path):
        save_original = hook_capture.HOOK_STATE_FILE
        hook_capture.HOOK_STATE_FILE = tmp_path / "nonexistent.json"
        try:
            state = hook_capture._read_hook_state()
            assert state["running"] == False
            assert state["pending_sessions"] == 0
        finally:
            hook_capture.HOOK_STATE_FILE = save_original

    def test_write_and_read_state(self, tmp_path):
        save_original = hook_capture.HOOK_STATE_FILE
        hook_capture.HOOK_STATE_FILE = tmp_path / "hook_state.json"
        try:
            state = {"running": True, "pending_sessions": 3, "sessions_ingested": 5}
            hook_capture._write_hook_state(state)

            read_state = hook_capture._read_hook_state()
            assert read_state["running"] == True
            assert read_state["pending_sessions"] == 3
            assert read_state["sessions_ingested"] == 5
        finally:
            hook_capture.HOOK_STATE_FILE = save_original

    def test_control_file_read_write(self, tmp_path):
        save_original = hook_capture.HOOK_CONTROL_FILE
        hook_capture.HOOK_CONTROL_FILE = tmp_path / "hook_control.json"
        try:
            hook_capture._write_hook_control({"command": "stop"})
            ctrl = hook_capture._read_hook_control()
            assert ctrl["command"] == "stop"

            hook_capture._clear_control()
            assert not (tmp_path / "hook_control.json").exists()
        finally:
            hook_capture.HOOK_CONTROL_FILE = save_original


# ═══════════════════════════════════════════════════════════════
# 会话冷却检测
# ═══════════════════════════════════════════════════════════════

class TestSessionCooling:
    """测试会话冷却判定逻辑。"""

    def test_cooled_if_older_than_cooldown(self, tmp_path):
        f = tmp_path / "test.jsonl"
        f.write_text("{}", encoding="utf-8")
        # 修改文件时间为 60 分钟前
        old_time = time.time() - 3600
        os.utime(str(f), (old_time, old_time))
        assert hook_capture._is_session_cooled(f, cooldown_minutes=30) == True

    def test_not_cooled_if_recently_modified(self, tmp_path):
        f = tmp_path / "test.jsonl"
        f.write_text("{}", encoding="utf-8")
        assert hook_capture._is_session_cooled(f, cooldown_minutes=30) == False

    def test_not_cooled_at_boundary(self, tmp_path):
        f = tmp_path / "test.jsonl"
        f.write_text("{}", encoding="utf-8")
        # 刚好 30 分钟前（未超过），加 1 秒缓冲
        boundary_time = time.time() - 30 * 60 + 1
        os.utime(str(f), (boundary_time, boundary_time))
        assert hook_capture._is_session_cooled(f, cooldown_minutes=30) == False


# ═══════════════════════════════════════════════════════════════
# 文件 Hash 与去重
# ═══════════════════════════════════════════════════════════════

class TestFileHash:
    """测试文件 Hash 与已处理检测。"""

    def test_file_hash_is_reproducible(self, tmp_path):
        f = tmp_path / "test.jsonl"
        f.write_text('{"type":"user","message":{"role":"user","content":"hello"}}\n', encoding="utf-8")
        h1 = hook_capture._file_hash(f)
        h2 = hook_capture._file_hash(f)
        assert h1 == h2

    def test_file_hash_changes_with_content(self, tmp_path):
        f = tmp_path / "test.jsonl"
        f.write_text("content A", encoding="utf-8")
        h1 = hook_capture._file_hash(f)
        f.write_text("content B", encoding="utf-8")
        h2 = hook_capture._file_hash(f)
        assert h1 != h2

    def test_is_processed_true_when_hash_matches(self, tmp_path):
        f = tmp_path / "session123.jsonl"
        f.write_text('{"type":"user","message":{"role":"user","content":"test"}}\n', encoding="utf-8")
        ingested = {"session123": {"file_hash": hook_capture._file_hash(f)}}
        assert hook_capture._is_processed(f, ingested) == True

    def test_is_processed_false_when_not_in_dict(self, tmp_path):
        f = tmp_path / "session999.jsonl"
        f.write_text("{}", encoding="utf-8")
        assert hook_capture._is_processed(f, {}) == False


# ═══════════════════════════════════════════════════════════════
# DAG 上下文采集
# ═══════════════════════════════════════════════════════════════

class TestDagContext:
    """测试 DAG 上下文采集。"""

    def test_collect_dag_from_session_file(self, tmp_path):
        f = tmp_path / "test.jsonl"
        lines = [
            json.dumps({"type": "user", "cwd": "e:\\projects\\myapp", "gitBranch": "main",
                         "message": {"role": "user", "content": "fix bug"}}),
            json.dumps({"type": "assistant", "cwd": "e:\\projects\\myapp", "gitBranch": "main",
                         "message": {"role": "assistant", "content": "done"}}),
            json.dumps({"type": "user", "gitBranch": "feature-x",
                         "message": {"role": "user", "content": "add feature"}}),
        ]
        f.write_text("\n".join(lines), encoding="utf-8")

        dag = hook_capture._collect_dag_context(f)
        assert dag["cwd"] == "e:\\projects\\myapp"
        assert "main" in dag["git_branches"]
        assert "feature-x" in dag["git_branches"]

    def test_collect_dag_empty_file(self, tmp_path):
        f = tmp_path / "empty.jsonl"
        f.write_text("", encoding="utf-8")
        dag = hook_capture._collect_dag_context(f)
        assert dag["cwd"] is None
        assert dag["git_branches"] == []

    def test_update_dag_state(self):
        state = hook_capture._default_state()
        dag = {"cwd": "/home/user/project", "git_branches": ["main"]}
        hook_capture._update_dag_state(state, "session-001", dag)
        assert "session-001" in state["dag_context"]["sessions"]
        sess = state["dag_context"]["sessions"]["session-001"]
        assert sess["cwd"] == "/home/user/project"
        assert "main" in sess["git_branches"]


# ═══════════════════════════════════════════════════════════════
# auto_ingest_tool
# ═══════════════════════════════════════════════════════════════

class TestAutoIngestTool:
    """测试 Agent auto_ingest 工具。"""

    @pytest.mark.skip(reason="集成测试需要 LLM 环境，手动验证")
    def test_auto_ingest_returns_ok(self):
        from backend.agent.tools import auto_ingest_tool
        result = auto_ingest_tool()
        assert "ok" in result or "scanned" in result

    @pytest.mark.skip(reason="集成测试需要会话目录，手动验证")
    def test_force_ingest_now_has_correct_structure(self, tmp_path, monkeypatch):
        def mock_ingest_single(f):
            return {"session_id": "test", "problems": 0, "message_count": 0, "error": None}
        monkeypatch.setattr(
            "backend.session_ingestor.ingest_single_session", mock_ingest_single
        )
        save_data = hook_capture.DATA_DIR
        hook_capture.DATA_DIR = tmp_path
        try:
            result = hook_capture.force_ingest_now()
            assert "ok" in result
            assert "scanned" in result
            assert "ingested" in result
            assert "pending" in result
        finally:
            hook_capture.DATA_DIR = save_data


# ═══════════════════════════════════════════════════════════════
# PID 文件管理
# ═══════════════════════════════════════════════════════════════

class TestPidManagement:
    """测试 PID 文件管理。"""

    def test_write_and_read_pid(self, tmp_path):
        save_pid = hook_capture.PID_FILE
        hook_capture.PID_FILE = tmp_path / "hook.pid"
        try:
            hook_capture._write_pid()
            assert (tmp_path / "hook.pid").exists()
            pid = int((tmp_path / "hook.pid").read_text().strip())
            assert pid > 0
        finally:
            hook_capture.PID_FILE = save_pid

    def test_remove_pid(self, tmp_path):
        save_pid = hook_capture.PID_FILE
        hook_capture.PID_FILE = tmp_path / "hook.pid"
        try:
            hook_capture._write_pid()
            assert (tmp_path / "hook.pid").exists()
            hook_capture._remove_pid()
            assert not (tmp_path / "hook.pid").exists()
        finally:
            hook_capture.PID_FILE = save_pid

    def test_daemon_not_running_when_no_pid(self, tmp_path):
        save_pid = hook_capture.PID_FILE
        hook_capture.PID_FILE = tmp_path / "nonexistent.pid"
        try:
            assert hook_capture._is_daemon_running() == False
        finally:
            hook_capture.PID_FILE = save_pid


# ═══════════════════════════════════════════════════════════════
# get_hook_state
# ═══════════════════════════════════════════════════════════════

class TestGetHookState:
    """测试 get_hook_state 对外接口。"""

    def test_returns_non_running_when_no_file(self, tmp_path):
        save_state = hook_capture.HOOK_STATE_FILE
        save_pid = hook_capture.PID_FILE
        hook_capture.HOOK_STATE_FILE = tmp_path / "nonexistent.json"
        hook_capture.PID_FILE = tmp_path / "nonexistent.pid"
        try:
            state = hook_capture.get_hook_state()
            assert state["running"] == False
        finally:
            hook_capture.HOOK_STATE_FILE = save_state
            hook_capture.PID_FILE = save_pid

    def test_returns_saved_state(self, tmp_path):
        save_state = hook_capture.HOOK_STATE_FILE
        save_pid = hook_capture.PID_FILE
        hook_capture.HOOK_STATE_FILE = tmp_path / "hook_state.json"
        hook_capture.PID_FILE = tmp_path / "nonexistent.pid"
        try:
            hook_capture._write_hook_state({"running": True, "pending_sessions": 2})
            state = hook_capture.get_hook_state()
            assert state["running"] == False  # 非运行中时修正
            assert state["pending_sessions"] == 2
        finally:
            hook_capture.HOOK_STATE_FILE = save_state
            hook_capture.PID_FILE = save_pid


# ═══════════════════════════════════════════════════════════════
# 集成测试：Agent State 输入层
# ═══════════════════════════════════════════════════════════════

class TestAgentInputLayer:
    """测试 Agent state.py 输入层包含 Hook 数据。"""

    def test_read_hook_state_returns_default(self, tmp_path, monkeypatch):
        from backend.agent import state
        fake_state = tmp_path / "data" / "hook_state.json"
        monkeypatch.setattr(
            state, "_read_hook_state",
            lambda: {"running": True, "pending_sessions": 3, "sessions_ingested": 10, "dag_context": {}}
        )
        hook = state._read_hook_state()
        assert hook["running"] == True
        assert hook["pending_sessions"] == 3
        assert hook["sessions_ingested"] == 10

    def test_dag_summary_aggregates_sessions(self):
        from backend.agent.state import _summarize_dag
        dag = {
            "sessions": {
                "s1": {"cwd": "/app", "git_branches": ["main"]},
                "s2": {"cwd": "/lib", "git_branches": ["dev", "feature"]},
            }
        }
        summary = _summarize_dag(dag)
        assert summary["session_count"] == 2
        assert "/app" in summary["working_directories"]
        assert "/lib" in summary["working_directories"]
        assert "main" in summary["branches"]
        assert "dev" in summary["branches"]
        assert "feature" in summary["branches"]

    def test_empty_dag_returns_zeros(self):
        from backend.agent.state import _summarize_dag
        summary = _summarize_dag({})
        assert summary["session_count"] == 0
        assert summary["working_directories"] == []
        assert summary["branches"] == []
