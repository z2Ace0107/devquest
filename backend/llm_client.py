# -*- coding: utf-8 -*-
"""
DevQuest — 统一 LLM 客户端

支持主备 Provider 自动切换：
  1. 优先使用 PRIMARY（opencode.ai DeepSeek V4 Flash）
  2. 失败时自动降级到 FALLBACK（旧 DeepSeek API）
  3. 5 分钟内不再重试已失败的 provider
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


def get_llm(temperature: float = 0.3) -> ChatOpenAI:
    """获取 LLM 客户端。优先 Primary，失败自动切 Fallback。"""
    for name, cfg in [("primary", PRIMARY), ("fallback", FALLBACK)]:
        if _is_provider_blocked(name):
            logger.info("LLM %s 在冷却中，跳过", name)
            continue
        if not cfg["api_key"]:
            logger.info("LLM %s 未配置 API Key，跳过", name)
            continue
        try:
            llm = _get_or_create(name, cfg, temperature)
            # 发送一条空测试验证连通性
            _probe_llm(llm)
            logger.info("LLM %s 就绪 (%s)", name, cfg["model"])
            return llm
        except Exception as exc:
            logger.warning("LLM %s 连接失败: %s", name, exc)
            _failed_providers[name] = time.time()

    # 全部失败 → 用 primary（即使可能失败，至少报错清晰）
    logger.error("所有 LLM provider 均不可用，回退到 primary")
    return _get_or_create("primary", PRIMARY, temperature)


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


def reset_cache():
    """清空 LLM 缓存（切换配置后调用）。"""
    _llm_cache.clear()
    _failed_providers.clear()


def get_active_provider() -> str:
    """返回当前正在使用的 provider 名称。"""
    if _is_provider_blocked("primary"):
        return "fallback"
    return "primary"
