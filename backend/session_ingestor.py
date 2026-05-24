# -*- coding: utf-8 -*-
"""
Claude Session 自动摄入引擎

扫描 ~/.claude/projects/ 中的 JSONL 会话文件，
自动提取技术问题并导入经验库。

核心流程:
1. 扫描指定项目的 session 目录
2. 过滤已处理的 session（去重）
3. 判定 session 冷却（文件静默 30min+ 视为对话结束）
4. 重构对话文本（过滤系统消息、非技术命令）
5. 调用 extractor 提取问题
6. 记录摄入状态，支持增量扫描
"""

import hashlib
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

# ── 项目根目录 ────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent

from dotenv import load_dotenv
load_dotenv(BASE_DIR / ".env")

from backend import extractor

# ── 日志 ───────────────────────────────────────────────────────
logger = logging.getLogger("session_ingestor")
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(
        "[%(asctime)s] %(levelname)s - %(message)s", datefmt="%H:%M:%S"
    ))
    logger.addHandler(handler)

# ── 配置 ───────────────────────────────────────────────────────
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_SESSIONS_DIR = os.getenv(
    "CLAUDE_SESSIONS_DIR",
    str(Path.home() / ".claude" / "projects"),
)
DEFAULT_PROJECT = os.getenv("WATCH_PROJECTS", "e--develop-claude")
COOLDOWN_MINUTES = int(os.getenv("INGEST_COOLDOWN_MINUTES", "30"))

STATE_FILE = DATA_DIR / "ingest_state.json"


# ── 状态管理 ───────────────────────────────────────────────────

def _load_state() -> dict:
    """加载摄入状态文件。"""
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save_state(state: dict):
    """保存摄入状态文件。"""
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ── 会话扫描 ───────────────────────────────────────────────────

def _scan_session_files(sessions_dir: Path) -> list[Path]:
    """
    扫描 sessions_dir 下所有 .jsonl 文件，
    按修改时间升序排列（最旧的优先处理）。
    """
    if not sessions_dir.exists():
        logger.warning("会话目录不存在: %s", sessions_dir)
        return []
    files = sorted(
        sessions_dir.glob("*.jsonl"),
        key=lambda f: f.stat().st_mtime,
    )
    logger.info("扫描到 %d 个会话文件", len(files))
    return files


def _file_hash(filepath: Path) -> str:
    """计算文件 MD5（用于检测内容变化）。"""
    return hashlib.md5(filepath.read_bytes()).hexdigest()


def _is_session_cooled(filepath: Path, cooldown_minutes: int) -> bool:
    """
    判定会话是否已冷却（距最后修改超过 cooldown_minutes 分钟）。
    冷却意味着用户已经结束该对话，不会再追加新消息。
    """
    mtime = filepath.stat().st_mtime
    age_seconds = time.time() - mtime
    cooled = age_seconds > cooldown_minutes * 60
    if not cooled:
        age_min = age_seconds / 60
        logger.debug("会话 %s 尚在活跃期 (%.1f 分钟前修改)", filepath.name, age_min)
    return cooled


def _is_already_ingested(filepath: Path, state: dict) -> bool:
    """
    检查会话是否已摄入。
    使用文件 hash 对比，hash 相同表示内容未变、无需重新处理。
    """
    project_state = state.get(DEFAULT_PROJECT, {})
    ingested = project_state.get("ingested_sessions", {})
    session_id = filepath.stem
    if session_id in ingested:
        stored_hash = ingested[session_id].get("file_hash", "")
        current_hash = _file_hash(filepath)
        if stored_hash == current_hash:
            return True
    return False


# ── 对话重构 ───────────────────────────────────────────────────

# 非技术命令关键词，包含这些的 user 消息将被跳过
NON_TECH_PATTERNS = [
    "/clear", "/config", "/plugin", "/theme",
    "/help", "/memory", "/compact",
]

# 消息类型直接跳过
SKIP_MESSAGE_TYPES = {"queue-operation"}


def _reconstruct_conversation(filepath: Path) -> tuple[str, int]:
    """
    从 JSONL 文件中重构可读的对话文本。

    过滤规则:
    - 跳过 queue-operation 类型的系统消息
    - 跳过 isMeta 标记的元数据消息
    - 跳过 local_command 输出
    - 跳过明显的非技术命令

    返回:
        (conversation_text, message_count): 对话文本和有效消息数
    """
    lines = []
    message_count = 0

    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue

            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue

            # 过滤消息类型
            msg_type = msg.get("type", "")
            if msg_type in SKIP_MESSAGE_TYPES:
                continue

            # 过滤元数据消息
            if msg.get("isMeta"):
                continue

            # 过滤 local_command 输出
            if msg.get("subtype") == "local_command":
                continue

            # 提取消息内容
            role = None
            content = None

            if msg_type == "user":
                role = "user"
                msg_content = msg.get("message", {}).get("content", "")
                # content 可能是字符串或数组（多模态）
                if isinstance(msg_content, list):
                    text_parts = []
                    for block in msg_content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            text_parts.append(block.get("text", ""))
                    content = "\n".join(text_parts)
                else:
                    content = str(msg_content)

            elif msg_type == "assistant":
                role = "assistant"
                msg_content = msg.get("message", {}).get("content", "")
                if isinstance(msg_content, list):
                    text_parts = []
                    for block in msg_content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            text_parts.append(block.get("text", ""))
                    content = "\n".join(text_parts)
                else:
                    content = str(msg_content)

            if not role or not content or not content.strip():
                continue

            # 过滤非技术用户命令
            if role == "user":
                content_stripped = content.strip()
                is_command = any(
                    content_stripped.startswith(p)
                    for p in NON_TECH_PATTERNS
                )
                if is_command:
                    continue
                # 跳过多余的空行和纯空白
                if len(content_stripped) < 3:
                    continue

            lines.append(f"[{role}]: {content.strip()}")
            message_count += 1

    conversation = "\n\n".join(lines)
    return conversation, message_count


# ── 核心摄入逻辑 ───────────────────────────────────────────────

def _ingest_session(filepath: Path) -> dict:
    """
    摄入单个会话文件：重构对话 → 调用提取器 → 记录状态。

    返回:
        dict: {"session_id": str, "message_count": int, "problems": int, "error": str|None}
    """
    session_id = filepath.stem
    result = {
        "session_id": session_id,
        "message_count": 0,
        "problems": 0,
        "error": None,
    }

    try:
        # 1. 重构对话
        conversation_text, msg_count = _reconstruct_conversation(filepath)
        result["message_count"] = msg_count
        if msg_count < 3:
            logger.info("会话 %s 有效消息不足 (%d 条)，跳过", session_id, msg_count)
            return result

        # 2. 调用提取器（传递 session_id 用于溯源）
        problems = extractor.extract_problems(
            conversation_text=conversation_text,
            project_name=DEFAULT_PROJECT,
            session_id=session_id,
        )
        result["problems"] = len(problems)
        logger.info("会话 %s 摄入完成: %d 条消息 → %d 个问题",
                     session_id, msg_count, len(problems))
        return result

    except Exception as e:
        result["error"] = str(e)
        logger.error("会话 %s 摄入失败: %s", session_id, e)
        return result


def ingest_once(
    sessions_dir: Optional[str] = None,
    project_name: Optional[str] = None,
    cooldown_minutes: Optional[int] = None,
    force: bool = False,
) -> dict:
    """
    单次扫描摄入：扫描会话目录，摄入所有已冷却且未处理过的会话。

    参数:
        sessions_dir: 会话目录路径，默认从环境变量读取
        project_name: 项目名称，默认从环境变量读取
        cooldown_minutes: 冷却时间（分钟），默认 30
        force: 强制重新摄入所有会话（忽略已处理标记）

    返回:
        dict: {"scanned": int, "ingested": int, "problems": int, "sessions": list}
    """
    global DEFAULT_PROJECT  # noqa: PLW0603
    if project_name:
        DEFAULT_PROJECT = project_name

    sd = Path(sessions_dir) if sessions_dir else Path(DEFAULT_SESSIONS_DIR)
    sd = sd / DEFAULT_PROJECT
    cd = cooldown_minutes if cooldown_minutes is not None else COOLDOWN_MINUTES

    logger.info("开始摄入扫描: %s (冷却=%dmin)", sd, cd)

    # 加载状态
    state = _load_state()

    # 扫描文件
    session_files = _scan_session_files(sd)
    if not session_files:
        return {"scanned": 0, "ingested": 0, "problems": 0, "sessions": []}

    # 逐个处理
    results = []
    total_problems = 0
    ingested_count = 0

    for filepath in session_files:
        # 去重检查
        if not force and _is_already_ingested(filepath, state):
            logger.debug("会话 %s 已处理，跳过", filepath.stem)
            continue

        # 冷却检查
        if not force and not _is_session_cooled(filepath, cd):
            logger.debug("会话 %s 未冷却，跳过", filepath.stem)
            continue

        # 摄入
        result = _ingest_session(filepath)
        results.append(result)
        total_problems += result["problems"]
        ingested_count += 1

        # 更新状态
        if result["error"] is None:
            project_state = state.setdefault(DEFAULT_PROJECT, {})
            ingested = project_state.setdefault("ingested_sessions", {})
            ingested[result["session_id"]] = {
                "file_hash": _file_hash(filepath),
                "message_count": result["message_count"],
                "problems_extracted": result["problems"],
                "ingested_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat() + "Z",
            }
            project_state["last_ingested_at"] = datetime.now(timezone.utc).replace(tzinfo=None).isoformat() + "Z"

    # 保存状态
    _save_state(state)

    logger.info("摄入扫描完成: 扫描 %d, 摄入 %d, 问题 %d",
                 len(session_files), ingested_count, total_problems)

    return {
        "scanned": len(session_files),
        "ingested": ingested_count,
        "problems": total_problems,
        "sessions": results,
    }


def ingest_all(
    sessions_dir: Optional[str] = None,
    project_name: Optional[str] = None,
) -> dict:
    """
    全量摄入：扫描所有会话文件（忽略冷却和已处理标记）。

    适用于首次运行或手动触发全量导入。
    """
    return ingest_once(
        sessions_dir=sessions_dir,
        project_name=project_name,
        cooldown_minutes=0,
        force=True,
    )


def ingest_incremental(
    sessions_dir: Optional[str] = None,
    project_name: Optional[str] = None,
) -> dict:
    """
    增量摄入：只处理冷却完成且未摄入过的会话。

    适用于定时任务（如每小时执行一次）。
    """
    return ingest_once(
        sessions_dir=sessions_dir,
        project_name=project_name,
        cooldown_minutes=COOLDOWN_MINUTES,
        force=False,
    )


def ingest_single_session(filepath: str) -> dict:
    """
    摄入单个会话文件。供 Hook 捕获引擎调用。

    参数:
        filepath: 会话 JSONL 文件绝对路径

    返回:
        dict: {"session_id": str, "problems": int, "message_count": int, "error": str|None}
    """
    return _ingest_session(Path(filepath))


def get_ingest_status(project_name: Optional[str] = None) -> dict:
    """
    查询当前摄入状态。

    返回:
        dict: {"project": str, "sessions_ingested": int, "total_problems": int,
               "last_ingested_at": str|None, "pending_sessions": int}
    """
    pn = project_name or DEFAULT_PROJECT
    state = _load_state()
    project_state = state.get(pn, {})
    ingested = project_state.get("ingested_sessions", {})

    # 统计待处理的会话
    sd = Path(DEFAULT_SESSIONS_DIR) / pn
    pending = 0
    if sd.exists():
        for filepath in sd.glob("*.jsonl"):
            session_id = filepath.stem
            if session_id not in ingested:
                if _is_session_cooled(filepath, COOLDOWN_MINUTES):
                    pending += 1

    total_problems = sum(
        s.get("problems_extracted", 0) for s in ingested.values()
    )

    return {
        "project": pn,
        "sessions_ingested": len(ingested),
        "total_problems_ingested": total_problems,
        "last_ingested_at": project_state.get("last_ingested_at"),
        "pending_sessions": pending,
    }
