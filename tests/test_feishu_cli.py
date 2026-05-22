# -*- coding: utf-8 -*-
"""DevQuest — feishu_cli 模块单元测试"""

import os
import re
from unittest.mock import patch, MagicMock

import pytest

from backend import feishu_cli
from backend.feishu_cli import (
    FeishuClient,
    _parse_inline,
    _md_to_blocks,
    _BOLD_RE,
    _CODE_RE,
    get_client,
    reset_client,
    BLOCK_TYPE_TEXT,
    BLOCK_TYPE_HEADING2,
    BLOCK_TYPE_HEADING3,
    BLOCK_TYPE_HEADING4,
    BLOCK_TYPE_BULLET,
    BLOCK_TYPE_DIVIDER,
    BLOCK_TYPE_QUOTE,
)


# ── FeishuClient ──────────────────────────────────────

def test_client_available():
    client = FeishuClient("app_xxx", "secret_xxx")
    assert client.available is True

    empty_client = FeishuClient("", "")
    assert empty_client.available is False


@patch.dict(os.environ, {"FEISHU_APP_ID": "app_test", "FEISHU_APP_SECRET": "sec_test"})
def test_is_configured_true():
    reset_client()
    assert FeishuClient.is_configured() is True


@patch.dict(os.environ, {}, clear=True)
def test_is_configured_false():
    reset_client()
    assert FeishuClient.is_configured() is False


@patch.dict(os.environ, {"FEISHU_APP_ID": "app_test", "FEISHU_APP_SECRET": "sec_test"})
def test_get_client_returns_same_instance():
    reset_client()
    c1 = get_client()
    c2 = get_client()
    assert c1 is c2
    assert c1.available is True


@patch.dict(os.environ, {}, clear=True)
def test_get_client_returns_none_when_not_configured():
    reset_client()
    assert get_client() is None


# ── Token 管理 ────────────────────────────────────────

@patch("backend.feishu_cli.requests.post")
def test_ensure_token_caches(mock_post):
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "code": 0,
        "tenant_access_token": "tok_test123",
        "expire": 7200,
    }
    mock_post.return_value = mock_resp

    client = FeishuClient("app_x", "secret_x")
    t1 = client._ensure_token()
    t2 = client._ensure_token()

    assert t1 == "tok_test123"
    assert t2 == t1
    mock_post.assert_called_once()


# ── _parse_inline ─────────────────────────────────────

def test_parse_plain_text():
    result = _parse_inline("这是普通文本")
    assert len(result) == 1
    assert result[0]["text_run"]["content"] == "这是普通文本"


def test_parse_bold_text():
    result = _parse_inline("这是 **粗体** 文本")
    assert len(result) == 3
    assert result[0]["text_run"]["content"] == "这是 "
    assert result[1]["text_run"]["content"] == "粗体"
    assert result[1]["text_run"]["text_element_style"]["bold"] is True
    assert result[2]["text_run"]["content"] == " 文本"


def test_parse_inline_code():
    result = _parse_inline("运行 `pip install` 命令")
    assert len(result) == 3
    assert result[1]["text_run"]["content"] == "pip install"
    assert result[1]["text_run"]["text_element_style"]["inline_code"] is True


def test_parse_multiple_formats():
    result = _parse_inline("**重要**: 使用 `devquest save` 命令")
    assert len(result) >= 3
    contents = "".join(e["text_run"]["content"] for e in result)
    assert "重要" in contents
    assert "devquest save" in contents


def test_parse_no_format():
    result = _parse_inline("")
    assert len(result) == 1
    assert result[0]["text_run"]["content"] == ""


# ── _md_to_blocks ─────────────────────────────────────

def test_heading_blocks():
    md = "## 标题\n\n正文内容"
    blocks = _md_to_blocks(md)
    assert len(blocks) == 2
    assert blocks[0]["block_type"] == BLOCK_TYPE_HEADING2
    assert blocks[0]["heading2"]["elements"][0]["text_run"]["content"] == "标题"
    assert blocks[1]["block_type"] == BLOCK_TYPE_TEXT


def test_multiple_headings():
    md = "## H2\n\n### H3\n\n#### H4"
    blocks = _md_to_blocks(md)
    assert blocks[0]["block_type"] == BLOCK_TYPE_HEADING2
    assert blocks[1]["block_type"] == BLOCK_TYPE_HEADING3
    assert blocks[2]["block_type"] == BLOCK_TYPE_HEADING4


def test_bullet_list():
    md = "## 列表\n\n- 项目一\n- 项目二"
    blocks = _md_to_blocks(md)
    bullet_blocks = [b for b in blocks if b["block_type"] == BLOCK_TYPE_BULLET]
    assert len(bullet_blocks) == 2
    assert bullet_blocks[0]["bullet"]["elements"][0]["text_run"]["content"] == "项目一"
    assert bullet_blocks[1]["bullet"]["elements"][0]["text_run"]["content"] == "项目二"


def test_divider():
    md = "## 分隔\n\n---\n\n结尾"
    blocks = _md_to_blocks(md)
    assert any(b["block_type"] == BLOCK_TYPE_DIVIDER for b in blocks)
    assert len(blocks) == 3


def test_quote():
    md = "> 这是一段引用"
    blocks = _md_to_blocks(md)
    assert blocks[0]["block_type"] == BLOCK_TYPE_QUOTE
    assert blocks[0]["quote"]["elements"][0]["text_run"]["content"] == "这是一段引用"


def test_compile_tool_like_content():
    md = """## Docker 经验

> 涵盖 3 条 Docker 相关经验。典型问题: 容器启动报错

**经验数**: 3 · **方案迭代**: v2

---

### 1. 容器启动报错
- **类型**: 配置错误
- **方案** (v1): 检查 Dockerfile CMD

### 2. 镜像构建失败
- **类型**: 构建错误
- **方案** (v1): 调整 RUN 顺序"""
    blocks = _md_to_blocks(md)
    assert len(blocks) > 0
    types = [b["block_type"] for b in blocks]
    assert BLOCK_TYPE_HEADING2 in types
    assert BLOCK_TYPE_QUOTE in types
    assert BLOCK_TYPE_DIVIDER in types
    assert BLOCK_TYPE_HEADING3 in types
    assert BLOCK_TYPE_BULLET in types


def test_regex_bold():
    matches = _BOLD_RE.findall("这是 **粗体** 和 **另一段粗体** 文本")
    assert matches == ["粗体", "另一段粗体"]


def test_regex_code():
    matches = _CODE_RE.findall("运行 `pip install` 和 `python main.py`")
    assert matches == ["pip install", "python main.py"]


# ── create_doc ─────────────────────────────────────────

@patch("backend.feishu_cli.FeishuClient._ensure_token")
@patch("backend.feishu_cli.requests.post")
def test_create_doc_success(mock_post, mock_token):
    mock_token.return_value = "tok_test"

    create_mock = MagicMock()
    create_mock.json.return_value = {
        "code": 0,
        "data": {"document": {"document_id": "doc_abc123", "url": "https://feishu.cn/docx/doc_abc123"}},
    }
    append_mock = MagicMock()
    append_mock.json.return_value = {"code": 0}

    mock_post.side_effect = [create_mock, append_mock]

    client = FeishuClient("app_x", "secret_x")
    result = client.create_doc("测试文档", "## 测试\n\n内容")

    assert result["doc_id"] == "doc_abc123"
    assert result["url"] == "https://feishu.cn/docx/doc_abc123"
    assert mock_post.call_count == 2


@patch("backend.feishu_cli.FeishuClient._ensure_token")
@patch("backend.feishu_cli.requests.post")
def test_create_doc_auth_failure(mock_post, mock_token):
    mock_token.return_value = "tok_test"

    mock_resp = MagicMock()
    mock_resp.json.return_value = {"code": 99991663, "msg": "app_id not found"}
    mock_post.return_value = mock_resp

    client = FeishuClient("app_x", "secret_x")
    result = client.create_doc("测试", "内容")

    assert "error" in result
