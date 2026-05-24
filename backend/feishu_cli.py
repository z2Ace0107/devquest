# -*- coding: utf-8 -*-
"""DevQuest — 飞书 CLI 封装

通过官方 lark-cli（@larksuite/cli）操作飞书文档。
认证由 lark-cli 自身管理（config init + auth login），
本模块仅做子进程调用和输出解析。
"""

import json
import logging
import os
import shutil
import time
from typing import Optional

logger = logging.getLogger(__name__)

_LARK_CMD = None
_FEISHU_DOMAIN = os.getenv("FEISHU_DOMAIN", "bytedance.feishu.cn")

# is_available() 缓存（5 分钟 TTL）
_available_cache: Optional[bool] = None
_available_cache_time: float = 0
_CACHE_TTL = 300


def _find_lark_cmd() -> list[str]:
    """查找 lark-cli 命令，优先全局安装（lark-cli），fallback 到 npx。

    Windows 下 .CMD 文件需用 shutil.which() 解析完整路径。
    """
    global _LARK_CMD
    if _LARK_CMD is not None:
        return _LARK_CMD

    lark = shutil.which("lark-cli")
    if lark:
        _LARK_CMD = [lark]
        return _LARK_CMD

    npx = shutil.which("npx") or shutil.which("npx.cmd")
    if npx:
        _LARK_CMD = [npx, "@larksuite/cli@latest"]
        return _LARK_CMD

    _LARK_CMD = ["lark-cli"]
    return _LARK_CMD


def _run_lark(args: list[str], input_text: str = "", timeout: int = 60) -> dict:
    """运行 lark-cli 命令并解析 JSON 输出。"""
    cmd = _find_lark_cmd() + args
    try:
        proc = subprocess.run(
            cmd,
            input=input_text,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
        )
    except FileNotFoundError:
        return {"error": "lark-cli 未安装，请运行: npx @larksuite/cli@latest install"}
    except subprocess.TimeoutExpired:
        return {"error": "lark-cli 命令超时"}

    if proc.returncode != 0:
        err = proc.stderr.strip() or proc.stdout.strip()
        return {"error": err[:500]}

    out = proc.stdout.strip()
    if not out:
        return {"ok": True}
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return {"ok": True, "raw": out}


import subprocess


def is_available() -> bool:
    """检查 lark-cli 是否已安装并认证（结果缓存 5 分钟）。"""
    global _available_cache, _available_cache_time
    now = time.time()
    if _available_cache is not None and (now - _available_cache_time) < _CACHE_TTL:
        return _available_cache
    result = _run_lark(["auth", "status"], timeout=10)
    _available_cache = "error" not in result
    _available_cache_time = now
    return _available_cache


def create_doc(title: str, content_md: str) -> dict:
    """创建飞书文档。

    参数:
        title: 文档标题
        content_md: Markdown 正文

    返回:
        {"doc_id": str, "url": str, "title": str}
    """
    result = _run_lark([
        "docs", "+create",
        "--api-version", "v2",
        "--title", title,
        "--markdown", "-",
    ], input_text=content_md)

    if "error" in result:
        return result

    data = result.get("data", {})
    doc = data.get("document", {})
    doc_id = doc.get("document_id") or data.get("document_id", "")
    doc_url = doc.get("url") or data.get("url", "")
    if not doc_url and doc_id:
        doc_url = f"https://{_FEISHU_DOMAIN}/docx/{doc_id}"

    logger.info("飞书文档创建成功: %s (%s)", title, doc_id)
    return {"doc_id": doc_id, "url": doc_url, "title": title}


def update_doc(doc_id: str, title: str, content_md: str) -> dict:
    """更新已有飞书文档。

    参数:
        doc_id: 飞书文档 ID
        title: 新标题
        content_md: Markdown 正文

    返回:
        {"doc_id": str, "url": str, "title": str}
    """
    result = _run_lark([
        "docs", "+update",
        "--api-version", "v2",
        "--doc", doc_id,
        "--markdown", "-",
        "--new-title", title,
        "--mode", "overwrite",
    ], input_text=content_md)

    if "error" in result:
        return result

    doc_url = f"https://{_FEISHU_DOMAIN}/docx/{doc_id}"
    logger.info("飞书文档已更新: %s (%s)", title, doc_id)
    return {"doc_id": doc_id, "url": doc_url, "title": title}


def _reset_cmd():
    """重置缓存的 lark-cli 命令路径和可用性状态（测试用）。"""
    global _LARK_CMD, _available_cache, _available_cache_time
    _LARK_CMD = None
    _available_cache = None
    _available_cache_time = 0