# -*- coding: utf-8 -*-
"""DevQuest — feishu_cli 单元测试（lark-cli 子进程封装）"""

import json
from unittest.mock import patch, MagicMock

from backend import feishu_cli


def _mock_run(returncode=0, stdout='{"ok": true}', stderr=""):
    mock = MagicMock()
    mock.returncode = returncode
    mock.stdout = stdout
    mock.stderr = stderr
    return mock


# ── is_available ─────────────────────────────────────

@patch("backend.feishu_cli.subprocess.run")
def test_is_available_true(mock_run):
    mock_run.return_value = _mock_run(0, '{"status": "logged in"}')
    feishu_cli._reset_cmd()
    assert feishu_cli.is_available() is True


@patch("backend.feishu_cli.subprocess.run")
def test_is_available_false_bad_status(mock_run):
    mock_run.return_value = _mock_run(1, stderr="not logged in")
    feishu_cli._reset_cmd()
    assert feishu_cli.is_available() is False


@patch("backend.feishu_cli.subprocess.run", side_effect=FileNotFoundError)
def test_is_available_false_not_installed(mock_run):
    feishu_cli._reset_cmd()
    assert feishu_cli.is_available() is False


@patch("backend.feishu_cli.subprocess.run")
def test_is_available_cached(mock_run):
    mock_run.return_value = _mock_run(0, '{"status": "logged in"}')
    feishu_cli._reset_cmd()
    assert feishu_cli.is_available() is True
    assert feishu_cli.is_available() is True
    assert mock_run.call_count == 1


# ── create_doc ────────────────────────────────────────

@patch("backend.feishu_cli.subprocess.run")
def test_create_doc_success(mock_run):
    mock_run.return_value = _mock_run(0, json.dumps({
        "code": 0,
        "data": {
            "document": {
                "document_id": "DocABC123",
                "url": "https://feishu.cn/docx/DocABC123",
            }
        }
    }))

    result = feishu_cli.create_doc("测试主题", "## 测试\n\n内容")

    assert result["doc_id"] == "DocABC123"
    assert result["url"] == "https://feishu.cn/docx/DocABC123"
    assert result["title"] == "测试主题"
    assert "error" not in result


@patch("backend.feishu_cli.subprocess.run")
def test_create_doc_uses_title_flag(mock_run):
    mock_run.return_value = _mock_run(0, json.dumps({
        "code": 0,
        "data": {"document": {"document_id": "DocXYZ", "url": "https://feishu.cn/docx/DocXYZ"}}
    }))

    md = "## Docker 经验\n\n- 容器启动报错\n- 镜像构建失败"
    result = feishu_cli.create_doc("Docker 经验", md)

    assert result["doc_id"] == "DocXYZ"
    call_args = mock_run.call_args
    cmd = call_args.args[0]
    assert "--title" in cmd
    idx = cmd.index("--title")
    assert cmd[idx + 1] == "Docker 经验"
    input_text = call_args.kwargs.get("input", "")
    assert "<title>" not in input_text
    assert "容器启动报错" in input_text


@patch("backend.feishu_cli.subprocess.run")
def test_create_doc_no_format_flag(mock_run):
    mock_run.return_value = _mock_run(0, json.dumps({
        "code": 0,
        "data": {"document": {"document_id": "Doc1", "url": "https://feishu.cn/docx/Doc1"}}
    }))

    feishu_cli.create_doc("标题", "内容")

    cmd = mock_run.call_args.args[0]
    assert "--format" not in cmd


@patch("backend.feishu_cli.subprocess.run")
def test_create_doc_cli_error(mock_run):
    mock_run.return_value = _mock_run(1, stderr="permission denied")
    result = feishu_cli.create_doc("测试", "内容")
    assert "error" in result


@patch("backend.feishu_cli.subprocess.run", side_effect=FileNotFoundError)
def test_create_doc_not_installed(mock_run):
    result = feishu_cli.create_doc("测试", "内容")
    assert "error" in result
    assert "未安装" in result["error"]


# ── update_doc ────────────────────────────────────────

@patch("backend.feishu_cli.subprocess.run")
def test_update_doc_success(mock_run):
    mock_run.return_value = _mock_run(0, json.dumps({"code": 0}))

    result = feishu_cli.update_doc("DocABC123", "更新标题", "## 新内容")

    assert result["doc_id"] == "DocABC123"
    assert "error" not in result
    cmd = mock_run.call_args.args[0]
    assert "docs" in cmd
    assert "+update" in cmd


@patch("backend.feishu_cli.subprocess.run")
def test_update_doc_uses_new_title_flag(mock_run):
    mock_run.return_value = _mock_run(0, json.dumps({"code": 0}))

    result = feishu_cli.update_doc("DocABC", "新标题", "## 正文")

    cmd = mock_run.call_args.args[0]
    assert "--new-title" in cmd
    idx = cmd.index("--new-title")
    assert cmd[idx + 1] == "新标题"
    input_text = mock_run.call_args.kwargs.get("input", "")
    assert "<title>" not in input_text


@patch("backend.feishu_cli.subprocess.run")
def test_update_doc_no_format_flag(mock_run):
    mock_run.return_value = _mock_run(0, json.dumps({"code": 0}))

    feishu_cli.update_doc("Doc1", "标题", "内容")

    cmd = mock_run.call_args.args[0]
    assert "--format" not in cmd


@patch("backend.feishu_cli.subprocess.run")
def test_update_doc_cli_error(mock_run):
    mock_run.return_value = _mock_run(1, stderr="doc not found")
    result = feishu_cli.update_doc("invalid", "标题", "内容")
    assert "error" in result


# ── _run_lark ─────────────────────────────────────────

@patch("backend.feishu_cli.subprocess.run", side_effect=FileNotFoundError)
def test_run_lark_not_installed(mock_run):
    result = feishu_cli._run_lark(["auth", "status"])
    assert "error" in result
    assert "未安装" in result["error"]