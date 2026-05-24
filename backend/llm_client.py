# -*- coding: utf-8 -*-
"""
DevQuest — 统一 LLM 客户端

支持主备 Provider 智能切换：
  1. 优先使用 PRIMARY（opencode.ai DeepSeek V4 Flash）
  2. 额度/速率限制时暂停自动切换，发通知等用户确认
  3. 用户确认后 → 自动降级到 FALLBACK（旧 DeepSeek API）
  4. 非额度错误（网络/超时等）自动降级不等待
  5. 5 分钟内不再重试已失败的 provider
"""

import logging
import os
import time
from typing import Optional

from dotenv import load_dotenv
load_dotenv()

from langchain_openai import ChatOpenAI

logger = logging.getLogger(__name__)

# ── Provider 配置 ──────────────────────────────────────────────
PRIMARY = {
    "api_key": os.getenv("LLM_PRIMARY_API_KEY", ""),
    "base_url": os.getenv("LLM_PRIMARY_BASE_URL", "https://opencode.ai/zen/go/v1"),
    "model": os.getenv("LLM_PRIMARY_MODEL", "deepseek-v4-flash"),
}
FALLBACK = {
    "api_key": os.getenv("LLM_FALLBACK_API_KEY", ""),
    "base_url": os.getenv("LLM_FALLBACK_BASE_URL", "https://api.deepseek.com/v1"),
    "model": os.getenv("LLM_FALLBACK_MODEL", "deepseek-chat"),
}

# ── 运行时状态 ─────────────────────────────────────────────────
_llm_cache: dict[str, ChatOpenAI] = {}  # provider → instance
_failed_providers: dict[str, float] = {}  # provider → timestamp of last failure
_RETRY_COOLDOWN = 300  # 5 分钟冷却

# ── 额度通知系统 ───────────────────────────────────────────────
_quota_notification: Optional[dict] = None  # 待确认的额度通知
_fallback_approved: bool = False  # 用户是否已确认使用 Fallback


def get_llm(temperature: float = 0.3) -> ChatOpenAI:
    """获取 LLM 客户端。优先 Primary，失败时按错误类型智能处理。"""
    for name, cfg in [("primary", PRIMARY), ("fallback", FALLBACK)]:
        if _is_provider_blocked(name):
            logger.info("LLM %s 在冷却中，跳过", name)
            continue
        if not cfg["api_key"]:
            logger.info("LLM %s 未配置 API Key，跳过", name)
            continue

        # 额度通知未确认时，不自动切 Fallback
        if name == "fallback" and not _can_use_fallback():
            logger.warning("LLM Fallback 等待用户确认中，暂不切换")
            continue

        try:
            llm = _get_or_create(name, cfg, temperature)
            _probe_llm(llm)
            logger.info("LLM %s 就绪 (%s)", name, cfg["model"])

            # Primary 恢复后清除通知
            if name == "primary" and _quota_notification:
                _clear_quota_notification()

            return llm
        except Exception as exc:
            error_msg = _summarize_error(exc)
            logger.warning("LLM %s 连接失败: %s", name, error_msg)
            _failed_providers[name] = time.time()

            if name == "primary" and _is_quota_or_rate_error(exc):
                _trigger_quota_notification(error_msg)
                _failed_providers.pop("primary", None)  # 不冷却 primary，等用户确认

    # 全部失败 → 用 primary（即使可能失败，至少报错清晰）
    logger.error("所有 LLM provider 均不可用，回退到 primary")
    return _get_or_create("primary", PRIMARY, temperature)


def _can_use_fallback() -> bool:
    """检查是否可以使用 Fallback。"""
    global _fallback_approved
    # 首次使用 fallback 时自动放行，额度通知触发的阻塞才需要确认
    if _quota_notification is None:
        return True
    return _fallback_approved


def _is_quota_or_rate_error(exc: Exception) -> bool:
    """检测是否为额度耗尽或速率限制错误。"""
    msg = str(exc).lower()
    indicators = [
        "429", "rate limit", "too many requests",
        "402", "payment required",
        "insufficient", "quota", "balance", "额度",
        "exhausted", "limit exceeded", "capacity",
        "billing", "account", "top up", "upgrade your plan",
    ]
    return any(ind in msg for ind in indicators)


def _trigger_quota_notification(error_msg: str):
    """触发额度耗尽通知。"""
    global _quota_notification, _fallback_approved
    _quota_notification = {
        "type": "quota_exhausted",
        "provider": "primary",
        "model": PRIMARY["model"],
        "error": error_msg,
        "triggered_at": time.time(),
        "fallback_model": FALLBACK["model"],
    }
    _fallback_approved = False
    logger.warning("LLM Primary 额度已耗尽！等待用户确认后切换到 Fallback (%s)", FALLBACK["model"])


def _clear_quota_notification():
    """清除额度通知（Primary 恢复时自动调用）。"""
    global _quota_notification, _fallback_approved
    _quota_notification = None
    _fallback_approved = False
    logger.info("LLM Primary 已恢复，通知已清除")


def get_quota_notification() -> Optional[dict]:
    """返回待处理的额度通知，无通知时返回 None。"""
    if _quota_notification is None:
        return None
    return {
        **_quota_notification,
        "acknowledged": _fallback_approved,
        "message": (
            "go 额度已耗尽（Primary: opencode.ai DeepSeek V4 Flash）。"
            "是否切换至直连 DeepSeek API（Fallback）？\n"
            "- 同意: acknowledge_quota(continue_fallback=True)\n"
            "- 拒绝: acknowledge_quota(continue_fallback=False)\n"
            "- 拒绝后将等待 Primary 恢复（5 分钟后自动重试）"
        ),
    }


def acknowledge_quota(continue_fallback: bool = True):
    """用户确认额度通知。

    参数:
        continue_fallback: True 同意使用 Fallback，False 拒绝（等待 Primary 恢复）
    """
    global _fallback_approved
    if continue_fallback:
        _fallback_approved = True
        _failed_providers.pop("primary", None)  # 清除 primary 冷却，让它也能重试
        logger.info("用户已确认切换到 Fallback")
    else:
        _fallback_approved = False
        _failed_providers["primary"] = 0  # 不清除，但给一个很旧的 timestamp
        _failed_providers["fallback"] = time.time()  # 冷却 fallback
        logger.info("用户拒绝切换，等待 Primary 恢复")


def get_llm_status() -> dict:
    """返回 LLM 提供商当前状态。"""
    primary_active = not _is_provider_blocked("primary")
    fallback_active = not _is_provider_blocked("fallback")

    status = {
        "primary": {
            "model": PRIMARY["model"],
            "available": primary_active and bool(PRIMARY["api_key"]),
            "blocked": _is_provider_blocked("primary"),
        },
        "fallback": {
            "model": FALLBACK["model"],
            "available": fallback_active and bool(FALLBACK["api_key"]),
            "blocked": _is_provider_blocked("fallback"),
        },
    }

    notification = get_quota_notification()
    if notification:
        status["pending_notification"] = notification

    return status


def _get_or_create(name: str, cfg: dict, temperature: float) -> ChatOpenAI:
    """缓存单例创建。"""
    cache_key = f"{name}@{temperature}"
    if cache_key not in _llm_cache:
        _llm_cache[cache_key] = ChatOpenAI(
            model=cfg["model"],
            api_key=cfg["api_key"],
            base_url=cfg["base_url"],
            temperature=temperature,
        )
    return _llm_cache[cache_key]


def _probe_llm(llm: ChatOpenAI) -> None:
    """发送最小请求验证连通性。"""
    from langchain_core.messages import HumanMessage
    llm.invoke([HumanMessage(content="respond with OK")])


def _is_provider_blocked(name: str) -> bool:
    """检查 provider 是否在冷却期内。"""
    t = _failed_providers.get(name)
    if t is None:
        return False
    return time.time() - t < _RETRY_COOLDOWN


def _summarize_error(exc: Exception) -> str:
    """提取错误摘要，截断过长信息。"""
    msg = str(exc)
    return msg[:300] if len(msg) > 300 else msg


def reset_cache():
    """清空 LLM 缓存和通知状态（切换配置后调用）。"""
    _llm_cache.clear()
    _failed_providers.clear()
    _clear_quota_notification()


def get_active_provider() -> str:
    """返回当前正在使用的 provider 名称。"""
    if _is_provider_blocked("primary"):
        return "fallback"
    if _quota_notification and _fallback_approved:
        return "fallback"
    return "primary"
