# -*- coding: utf-8 -*-
"""DevQuest — 飞书 Open API 客户端

通过飞书 Server API 创建/更新飞书文档，将 compile_tool 编译的
Markdown 内容转为飞书 Doc 块结构并推送。

认证方式: App ID + App Secret → tenant_access_token（自动刷新）
"""

import json
import logging
import os
import re
import time
from typing import Optional

import requests

logger = logging.getLogger(__name__)

FEISHU_API_HOST = "https://open.feishu.cn/open-apis"


class FeishuClient:
    """飞书 Open API 客户端 — 管理文档创建和内容写入。"""

    def __init__(self, app_id: str, app_secret: str):
        self.app_id = app_id
        self.app_secret = app_secret
        self._token: Optional[str] = None
        self._token_expires_at: float = 0

    @property
    def available(self) -> bool:
        return bool(self.app_id and self.app_secret)

    @staticmethod
    def is_configured() -> bool:
        app_id = os.getenv("FEISHU_APP_ID", "")
        app_secret = os.getenv("FEISHU_APP_SECRET", "")
        return bool(app_id and app_secret)

    # ── Token 管理 ──────────────────────────────────────

    def _ensure_token(self) -> str:
        now = time.time()
        if self._token and now < self._token_expires_at - 60:
            return self._token

        resp = requests.post(
            f"{FEISHU_API_HOST}/auth/v3/tenant_access_token/internal",
            json={"app_id": self.app_id, "app_secret": self.app_secret},
            timeout=10,
        )
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"获取 tenant_access_token 失败: {data.get('msg', data)}")

        self._token = data["tenant_access_token"]
        self._token_expires_at = now + data.get("expire", 7200)
        logger.info("Feishu token 已刷新，有效期 %s 秒", data.get("expire"))
        return self._token

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._ensure_token()}",
            "Content-Type": "application/json",
        }

    # ── 文档操作 ────────────────────────────────────────

    def create_doc(self, title: str, content: str) -> dict:
        """创建飞书文档并写入 Markdown 内容。

        参数:
            title: 文档标题（显示在飞书文档列表中）
            content: Markdown 格式的文档正文

        返回:
            {"doc_id": str, "url": str, "title": str}
        """
        if not self.available:
            return {"error": "飞书 App ID / App Secret 未配置"}

        # Step 1: 创建空白文档
        create_resp = requests.post(
            f"{FEISHU_API_HOST}/docx/v1/documents",
            headers=self._headers(),
            json={"title": title},
            timeout=15,
        )
        create_data = create_resp.json()
        if create_data.get("code") != 0:
            return {"error": f"创建文档失败: {create_data.get('msg', create_data)}"}

        doc = create_data["data"]["document"]
        doc_id = doc["document_id"]
        doc_url = doc.get("url", f"https://bytedance.feishu.cn/docx/{doc_id}")

        # Step 2: 将 Markdown 转为块并写入
        blocks = _md_to_blocks(content)
        if blocks:
            append_resp = requests.post(
                f"{FEISHU_API_HOST}/docx/v1/documents/{doc_id}/blocks/{doc_id}/children",
                headers=self._headers(),
                json={"children": blocks, "index": -1},
                timeout=30,
            )
            append_data = append_resp.json()
            if append_data.get("code") != 0:
                return {
                    "doc_id": doc_id,
                    "url": doc_url,
                    "title": title,
                    "warning": f"文档创建成功但内容写入失败: {append_data.get('msg')}",
                }

        logger.info("飞书文档创建成功: %s (%s)", title, doc_id)
        return {"doc_id": doc_id, "url": doc_url, "title": title}

    def update_doc(self, doc_id: str, title: str, content: str) -> dict:
        """更新已有飞书文档的标题和内容。

        先替换标题，再全量替换正文（先清空再写入）。

        参数:
            doc_id: 飞书文档 ID
            title: 新标题
            content: Markdown 格式的新内容

        返回:
            {"doc_id": str, "url": str, "title": str}
        """
        if not self.available:
            return {"error": "飞书 App ID / App Secret 未配置"}

        headers = self._headers()

        # 更新标题
        requests.patch(
            f"{FEISHU_API_HOST}/docx/v1/documents/{doc_id}",
            headers=headers,
            json={"title": title},
            timeout=10,
        )

        # 获取现有块列表（页面根节点的子块）
        get_blocks = requests.get(
            f"{FEISHU_API_HOST}/docx/v1/documents/{doc_id}/blocks/{doc_id}/children",
            headers=headers,
            timeout=10,
        )
        blocks_data = get_blocks.json()
        existing_children = []
        if blocks_data.get("code") == 0:
            existing_children = blocks_data.get("data", {}).get("items", [])

        # 删除现有子块
        if existing_children:
            child_ids = [b["block_id"] for b in existing_children if b.get("block_type") != 1 or b.get("text")]
            for child_id in child_ids:
                requests.delete(
                    f"{FEISHU_API_HOST}/docx/v1/documents/{doc_id}/blocks/{child_id}",
                    headers=headers,
                    timeout=5,
                )

        # 写入新内容
        blocks = _md_to_blocks(content)
        if blocks:
            requests.post(
                f"{FEISHU_API_HOST}/docx/v1/documents/{doc_id}/blocks/{doc_id}/children",
                headers=headers,
                json={"children": blocks, "index": -1},
                timeout=30,
            )

        doc_url = f"https://bytedance.feishu.cn/docx/{doc_id}"
        logger.info("飞书文档已更新: %s (%s)", title, doc_id)
        return {"doc_id": doc_id, "url": doc_url, "title": title}


# ── 单例 ──────────────────────────────────────────────────────────

_client: Optional[FeishuClient] = None


def get_client() -> Optional[FeishuClient]:
    global _client
    if _client is None and FeishuClient.is_configured():
        _client = FeishuClient(
            app_id=os.getenv("FEISHU_APP_ID", ""),
            app_secret=os.getenv("FEISHU_APP_SECRET", ""),
        )
    return _client


def reset_client():
    global _client
    _client = None


# ── Markdown → 飞书块 转换 ────────────────────────────────────────

BLOCK_TYPE_TEXT = 1
BLOCK_TYPE_HEADING2 = 4
BLOCK_TYPE_HEADING3 = 5
BLOCK_TYPE_HEADING4 = 6
BLOCK_TYPE_BULLET = 9
BLOCK_TYPE_DIVIDER = 26
BLOCK_TYPE_QUOTE = 28

_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_CODE_RE = re.compile(r"`([^`]+)`")


def _parse_inline(text: str) -> list[dict]:
    """将带 **粗体** 和 `代码` 的行内文本解析为 elements 列表。"""
    elements = []
    pos = 0

    while pos < len(text):
        bold_match = _BOLD_RE.search(text, pos)
        code_match = _CODE_RE.search(text, pos)

        next_match = None
        match_type = None
        for m, t in [(bold_match, "bold"), (code_match, "code")]:
            if m and (next_match is None or m.start() < next_match.start()):
                next_match = m
                match_type = t

        if next_match is None:
            elements.append({"text_run": {"content": text[pos:]}})
            break

        if next_match.start() > pos:
            elements.append({"text_run": {"content": text[pos:next_match.start()]}})

        if match_type == "bold":
            elements.append({
                "text_run": {
                    "content": next_match.group(1),
                    "text_element_style": {"bold": True},
                }
            })
        elif match_type == "code":
            elements.append({
                "text_run": {
                    "content": next_match.group(1),
                    "text_element_style": {"inline_code": True},
                }
            })

        pos = next_match.end()

    return elements if elements else [{"text_run": {"content": text}}]


def _md_to_blocks(md_text: str) -> list[dict]:
    """将 Markdown 文本转为飞书 Doc 块列表。"""
    blocks = []
    lines = md_text.split("\n")
    pending_text = []

    i = 0
    while i < len(lines):
        line = lines[i].rstrip()

        if not line:
            if pending_text:
                blocks.append({
                    "block_type": BLOCK_TYPE_TEXT,
                    "text": {"elements": _parse_inline("\n".join(pending_text))},
                })
                pending_text = []
            i += 1
            continue

        # --- → divider
        if line.strip() == "---":
            if pending_text:
                blocks.append({
                    "block_type": BLOCK_TYPE_TEXT,
                    "text": {"elements": _parse_inline("\n".join(pending_text))},
                })
                pending_text = []
            blocks.append({"block_type": BLOCK_TYPE_DIVIDER, "divider": {}})
            i += 1
            continue

        # ## heading
        heading_match = re.match(r"^(#{2,4})\s+(.+)", line)
        if heading_match:
            if pending_text:
                blocks.append({
                    "block_type": BLOCK_TYPE_TEXT,
                    "text": {"elements": _parse_inline("\n".join(pending_text))},
                })
                pending_text = []
            level = len(heading_match.group(1))
            content = heading_match.group(2)
            block_type_map = {2: BLOCK_TYPE_HEADING2, 3: BLOCK_TYPE_HEADING3, 4: BLOCK_TYPE_HEADING4}
            bt = block_type_map.get(level, BLOCK_TYPE_HEADING2)
            heading_key_map = {BLOCK_TYPE_HEADING2: "heading2", BLOCK_TYPE_HEADING3: "heading3", BLOCK_TYPE_HEADING4: "heading4"}
            blocks.append({
                "block_type": bt,
                heading_key_map[bt]: {"elements": _parse_inline(content)},
            })
            i += 1
            continue

        # - item
        bullet_match = re.match(r"^[-*]\s+(.+)", line)
        if bullet_match:
            if pending_text:
                blocks.append({
                    "block_type": BLOCK_TYPE_TEXT,
                    "text": {"elements": _parse_inline("\n".join(pending_text))},
                })
                pending_text = []
            blocks.append({
                "block_type": BLOCK_TYPE_BULLET,
                "bullet": {"elements": _parse_inline(bullet_match.group(1))},
            })
            i += 1
            continue

        # > quote
        quote_match = re.match(r"^>\s?(.*)", line)
        if quote_match:
            if pending_text:
                blocks.append({
                    "block_type": BLOCK_TYPE_TEXT,
                    "text": {"elements": _parse_inline("\n".join(pending_text))},
                })
                pending_text = []
            blocks.append({
                "block_type": BLOCK_TYPE_QUOTE,
                "quote": {"elements": _parse_inline(quote_match.group(1))},
            })
            i += 1
            continue

        # 普通文本
        pending_text.append(line)
        i += 1

    if pending_text:
        blocks.append({
            "block_type": BLOCK_TYPE_TEXT,
            "text": {"elements": _parse_inline("\n".join(pending_text))},
        })

    return blocks
