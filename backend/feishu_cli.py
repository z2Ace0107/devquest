# -*- coding: utf-8 -*-
"""DevQuest — 飞书 CLI 封装

通过官方 lark-cli（@larksuite/cli）操作飞书文档。
认证由 lark-cli 自身管理（config init + auth login），
本模块仅做子进程调用和输出解析。
"""

import json
import logging
import subprocess
import sys
from typing import Optional

logger = logging.getLogger(__name__)

_LARK_CLI_CMD = "lark-cli"


def _run_lark(args: list[str], input_text: str = "") -> dict:
    """运行 lark-cli 命令并解析 JSON 输出。"""
    try:
        proc = subprocess.run(
            [_LARK_CLI_CMD] + args,
            input=input_text,
            capture_output=True,
            text=True,
            timeout=30,
            encoding="utf-8",
        )
    except FileNotFoundError:
        return {"error": "lark-cli 未安装，请运行: npx @larksuite/cli@latest install"}
    except subprocess.TimeoutExpired:
        return {"error": "lark-cli 命令超时"}

    if proc.returncode != 0:
        err = proc.stderr.strip() or proc.stdout.strip()
        return {"error": err[:500]}

    # 解析 JSON 输出
    out = proc.stdout.strip()
    if not out:
        return {"ok": True}
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return {"ok": True, "raw": out}


def is_available() -> bool:
    """检查 lark-cli 是否已安装并认证。"""
    result = _run_lark(["auth", "status"])
    return "error" not in result


def create_doc(title: str, content_md: str) -> dict:
    """创建飞书文档。

    参数:
        title: 文档标题
        content_md: Markdown 正文

    返回:
        {"doc_id": str, "url": str, "title": str}
    """
    # 构建完整 Markdown：lark-cli docs +create 的 --markdown 需要
    # Lark-flavored Markdown，用 <title> 标签指定标题
    full_md = f"<title>{title}</title>\n{content_md}"

    result = _run_lark([
        "docs", "+create",
        "--api-version", "v2",
        "--markdown", "-",
        "--format", "json",
    ], input_text=full_md)

    if "error" in result:
        return result

    # 解析输出，获取文档 ID 和 URL
    # lark-cli 返回 {code, data: {document: {document_id, url, ...}}}
    data = result.get("data", {})
    doc = data.get("document", {})
    doc_id = doc.get("document_id") or data.get("document_id", "")
    doc_url = doc.get("url") or data.get("url", "")
    if not doc_url and doc_id:
        doc_url = f"https://bytedance.feishu.cn/docx/{doc_id}"

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
    full_md = f"<title>{title}</title>\n{content_md}"

    result = _run_lark([
        "docs", "+update",
        "--api-version", "v2",
        "--doc", doc_id,
        "--markdown", "-",
        "--new-title", title,
        "--mode", "overwrite",
        "--format", "json",
    ], input_text=full_md)

    if "error" in result:
        return result

    doc_url = f"https://bytedance.feishu.cn/docx/{doc_id}"
    logger.info("飞书文档已更新: %s (%s)", title, doc_id)
    return {"doc_id": doc_id, "url": doc_url, "title": title}
