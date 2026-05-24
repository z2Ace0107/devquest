# -*- coding: utf-8 -*-
"""DevQuest — llm_client 额度通知系统单测"""

import json
from unittest.mock import patch, MagicMock

import pytest

from backend import llm_client
from langchain_openai import ChatOpenAI


def _mock_llm_resp(content="OK"):
    mock_llm = MagicMock(spec=ChatOpenAI)
    mock_msg = MagicMock()
    mock_msg.content = content
    mock_llm.invoke.return_value = mock_msg
    return mock_llm


def _mock_error(status_code=429, error_msg="rate limit exceeded"):
    """模拟 API 错误（HTTP 状态码形式）。"""
    import requests
    resp = requests.Response()
    resp.status_code = status_code
    resp._content = json.dumps({"error": {"message": error_msg}}).encode()
    from requests.exceptions import HTTPError
    exc = HTTPError(error_msg, response=resp)
    return exc


def setup_function():
    llm_client.reset_cache()


# ── 基础 ──────────────────────────────────────────

@patch("backend.llm_client._probe_llm")
def test_get_llm_primary_success(mock_probe):
    llm_mock = _mock_llm_resp()
    mock_probe.return_value = None
    with patch.object(llm_client, "_get_or_create", return_value=llm_mock):
        result = llm_client.get_llm()
        assert result is llm_mock
        assert llm_client.get_active_provider() == "primary"


@patch("backend.llm_client._probe_llm")
def test_get_llm_status_idle(mock_probe):
    llm_mock = _mock_llm_resp()
    mock_probe.return_value = None
    with patch.object(llm_client, "_get_or_create", return_value=llm_mock):
        llm_client.get_llm()  # 先初始化为可用
    status = llm_client.get_llm_status()
    assert status["primary"]["available"] is True
    assert "pending_notification" not in status


# ── 额度检测 ────────────────────────────────────────

def test_is_quota_rate_limit():
    exc = _mock_error(429, "Rate limit exceeded")
    assert llm_client._is_quota_or_rate_error(exc) is True


def test_is_quota_insufficient():
    exc = _mock_error(403, "insufficient quota balance")
    assert llm_client._is_quota_or_rate_error(exc) is True


def test_is_quota_billing():
    exc = _mock_error(402, "payment required - billing issue")
    assert llm_client._is_quota_or_rate_error(exc) is True


def test_is_not_quota_timeout():
    exc = _mock_error(500, "internal server error")
    assert llm_client._is_quota_or_rate_error(exc) is False


def test_is_quota_string_error():
    exc = Exception("Insufficient quota. Please top up your account.")
    assert llm_client._is_quota_or_rate_error(exc) is True


def test_is_quota_limit_exceeded():
    exc = Exception("limit exceeded: capacity reached")
    assert llm_client._is_quota_or_rate_error(exc) is True


def test_is_not_quota_generic_error():
    exc = Exception("connection refused")
    assert llm_client._is_quota_or_rate_error(exc) is False


# ── 通知触发 ──────────────────────────────────────

@patch("backend.llm_client._probe_llm")
def test_quota_triggers_notification(mock_probe):
    exc = _mock_error(429, "Rate limit exceeded, quota exhausted")
    mock_probe.side_effect = [exc, None]  # primary fails, fallback succeeds
    llm_mock = _mock_llm_resp()

    with patch.object(llm_client, "_get_or_create", return_value=llm_mock):
        result = llm_client.get_llm()

    notification = llm_client.get_quota_notification()
    assert notification is not None
    assert notification["type"] == "quota_exhausted"
    assert notification["acknowledged"] is False
    # 应该已经收到 primary 失败的通知
    assert "Rate limit" in notification["error"]


def test_no_notification_initially():
    assert llm_client.get_quota_notification() is None


@patch("backend.llm_client._probe_llm")
def test_quota_notification_appears_in_status(mock_probe):
    exc = _mock_error(429, "Rate limit exceeded, quota exhausted")
    mock_probe.side_effect = [exc, None]
    llm_mock = _mock_llm_resp()

    with patch.object(llm_client, "_get_or_create", return_value=llm_mock):
        llm_client.get_llm()

    status = llm_client.get_llm_status()
    assert "pending_notification" in status
    assert status["pending_notification"]["type"] == "quota_exhausted"


# ── 用户确认 ───────────────────────────────────────

@patch("backend.llm_client._probe_llm")
def test_acknowledge_quota_continue(mock_probe):
    exc = _mock_error(429, "Rate limit exceeded")
    mock_probe.side_effect = [exc, None]
    llm_mock = _mock_llm_resp()

    with patch.object(llm_client, "_get_or_create", return_value=llm_mock):
        llm_client.get_llm()

    assert llm_client.get_quota_notification() is not None

    llm_client.acknowledge_quota(continue_fallback=True)

    notification = llm_client.get_quota_notification()
    assert notification["acknowledged"] is True
    assert notification["message"]  # 包含切换提示


@patch("backend.llm_client._probe_llm")
def test_acknowledge_quota_reject(mock_probe):
    exc = _mock_error(429, "Rate limit exceeded")
    mock_probe.side_effect = exc
    llm_mock = _mock_llm_resp()

    with patch.object(llm_client, "_get_or_create", return_value=llm_mock):
        try:
            llm_client.get_llm()
        except Exception:
            pass

    llm_client.acknowledge_quota(continue_fallback=False)

    notification = llm_client.get_quota_notification()
    assert notification is not None
    assert notification["acknowledged"] is False


def test_acknowledge_when_no_notification():
    assert llm_client.get_quota_notification() is None
    # 不应崩溃
    llm_client.acknowledge_quota(continue_fallback=True)


# ── reset_cache 清除通知 ────────────────────────────

@patch("backend.llm_client._probe_llm")
def test_reset_cache_clears_notification(mock_probe):
    exc = _mock_error(429, "Rate limit exceeded")
    mock_probe.side_effect = [exc, None]
    llm_mock = _mock_llm_resp()

    with patch.object(llm_client, "_get_or_create", return_value=llm_mock):
        llm_client.get_llm()

    assert llm_client.get_quota_notification() is not None

    llm_client.reset_cache()

    assert llm_client.get_quota_notification() is None
