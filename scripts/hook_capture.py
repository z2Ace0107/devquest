# -*- coding: utf-8 -*-
"""
DevQuest Hook 自动捕获引擎 (V4.2)

后台轮询守护进程，自动检测 Claude 会话结束并触发经验摄入。
会话"冷却"（30min 无写入）即视为结束，自动调用 session_ingestor 入库。

架构：
  hook_capture.py (后台进程)
      │
      ├── 轮询 ~/.claude/projects/ 中的 JSONL 文件
      ├── 检测冷却会话 → 触发 ingest
      ├── 收集 DAG 上下文（git 信息）
      └── 写入 data/hook_state.json（供 Agent/MCP 读取）

启动方式:
  python scripts/hook_capture.py                     # 前台运行
  python scripts/hook_capture.py --daemon             # 后台运行（写入 PID 文件）

MCP 通过读取 data/hook_state.json 了解 Hook 状态，
通过写入 data/hook_control.json 控制 Hook 启停。
"""

import argparse
import hashlib
import json
import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

# ── 路径初始化 ──────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).resolve().parent
BASE_DIR = SCRIPT_DIR.parent
sys.path.insert(0, str(BASE_DIR))

from dotenv import load_dotenv
load_dotenv(BASE_DIR / ".env")

DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
HOOK_STATE_FILE = DATA_DIR / "hook_state.json"
HOOK_CONTROL_FILE = DATA_DIR / "hook_control.json"
PID_FILE = DATA_DIR / "hook.pid"

# ── 配置 ──────────────────────────────────────────────────────
DEFAULT_SESSIONS_DIR = os.getenv(
    "CLAUDE_SESSIONS_DIR",
    str(Path.home() / ".claude" / "projects"),
)
DEFAULT_PROJECT = os.getenv("WATCH_PROJECTS", "e--develop-claude")
COOLDOWN_MINUTES = int(os.getenv("INGEST_COOLDOWN_MINUTES", "30"))
POLL_INTERVAL_SECONDS = int(os.getenv("HOOK_POLL_INTERVAL", "60"))

# ── 日志 ──────────────────────────────────────────────────────
logger = logging.getLogger("hook_capture")
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [HOOK] %(levelname)s - %(message)s", datefmt="%H:%M:%S"
    ))
    logger.addHandler(handler)


# ═══════════════════════════════════════════════════════════════
# 状态文件读写
# ═══════════════════════════════════════════════════════════════

def _read_hook_state() -> dict:
    """读取 Hook 状态文件。"""
    if HOOK_STATE_FILE.exists():
        try:
            return json.loads(HOOK_STATE_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return _default_state()


def _default_state() -> dict:
    return {
        "running": False,
        "pid": None,
        "started_at": None,
        "last_scan_at": None,
        "sessions_scanned": 0,
        "sessions_ingested": 0,
        "pending_sessions": 0,
        "last_ingested_session": None,
        "dag_context": {},
        "errors": [],
    }


def _write_hook_state(state: dict):
    """写入 Hook 状态文件。"""
    HOOK_STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _read_hook_control() -> dict:
    """读取 Hook 控制文件。"""
    if HOOK_CONTROL_FILE.exists():
        try:
            return json.loads(HOOK_CONTROL_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {"command": None}


def _write_hook_control(control: dict):
    """写入 Hook 控制文件。"""
    HOOK_CONTROL_FILE.write_text(
        json.dumps(control, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _clear_control():
    """清除控制命令（已处理）。"""
    if HOOK_CONTROL_FILE.exists():
        HOOK_CONTROL_FILE.unlink(missing_ok=True)


# ═══════════════════════════════════════════════════════════════
# DAG 上下文采集
# ═══════════════════════════════════════════════════════════════

def _collect_dag_context(filepath: Path) -> dict:
    """从会话文件中采集 DAG 上下文（git 分支、变更文件等）。"""
    dag = {
        "cwd": None,
        "git_branches": [],
        "last_commit_messages": [],
    }
    seen_branches = set()

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue

                cwd = msg.get("cwd")
                if cwd and not dag["cwd"]:
                    dag["cwd"] = cwd

                branch = msg.get("gitBranch")
                if branch and branch not in seen_branches:
                    seen_branches.add(branch)
                    dag["git_branches"].append(branch)
    except OSError:
        pass

    return dag


def _update_dag_state(state: dict, session_id: str, dag: dict):
    """将单次会话的 DAG 上下文合并入全局状态。"""
    dag_state = state.setdefault("dag_context", {})
    dag_state.setdefault("sessions", {})
    dag_state["sessions"][session_id] = {
        "cwd": dag.get("cwd"),
        "git_branches": dag.get("git_branches", []),
        "captured_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat() + "Z",
    }


# ═══════════════════════════════════════════════════════════════
# 会话扫描与冷却检测
# ═══════════════════════════════════════════════════════════════

def _file_hash(filepath: Path) -> str:
    return hashlib.md5(filepath.read_bytes()).hexdigest()


def _is_session_cooled(filepath: Path, cooldown_minutes: int) -> bool:
    """文件最后修改距今超过 cooldown_minutes → 会话已结束。"""
    mtime = filepath.stat().st_mtime
    return (time.time() - mtime) > cooldown_minutes * 60


def _is_processed(filepath: Path, ingested: dict) -> bool:
    """检查会话是否已处理过（hash 对比）。"""
    session_id = filepath.stem
    if session_id in ingested:
        stored = ingested[session_id].get("file_hash", "")
        if stored == _file_hash(filepath):
            return True
    return False


def _scan_and_ingest(
    sessions_dir: Path,
    cooldown_minutes: int,
    state: dict,
) -> dict:
    """单次扫描：检测冷却会话 → 触发摄入。"""
    from backend import session_ingestor

    if not sessions_dir.exists():
        return {"scanned": 0, "ingested": 0, "pending": 0}

    files = sorted(sessions_dir.glob("*.jsonl"), key=lambda f: f.stat().st_mtime)

    ingested = state.get("ingested_sessions", {})

    scanned = 0
    ingested_count = 0
    pending = 0

    for filepath in files:
        scanned += 1

        # 已处理过，跳过
        if _is_processed(filepath, ingested):
            continue

        # 未冷却（仍在活跃对话中），计入待处理
        if not _is_session_cooled(filepath, cooldown_minutes):
            pending += 1
            continue

        # 冷却完成 → 自动摄入
        session_id = filepath.stem
        logger.info("检测到已冷却会话: %s", session_id)

        # 收集 DAG 上下文
        dag = _collect_dag_context(filepath)
        _update_dag_state(state, session_id, dag)

        # 执行摄入
        try:
            result = session_ingestor.ingest_single_session(str(filepath))
            logger.info("会话 %s 摄入完成: %s", session_id,
                         result.get("problems", 0) if isinstance(result, dict) else "OK")

            ingested[session_id] = {
                "file_hash": _file_hash(filepath),
                "ingested_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat() + "Z",
            }
            state["ingested_sessions"] = ingested
            ingested_count += 1
            state["last_ingested_session"] = session_id
        except Exception as e:
            logger.exception("会话 %s 摄入失败: %s", session_id, e)
            state.setdefault("errors", []).append({
                "session_id": session_id,
                "error": str(e),
                "timestamp": datetime.now(timezone.utc).replace(tzinfo=None).isoformat() + "Z",
            })

    # 更新统计
    state["sessions_scanned"] = state.get("sessions_scanned", 0) + scanned
    state["sessions_ingested"] = state.get("sessions_ingested", 0) + ingested_count
    state["pending_sessions"] = pending
    state["last_scan_at"] = datetime.now(timezone.utc).replace(tzinfo=None).isoformat() + "Z"

    return {"scanned": scanned, "ingested": ingested_count, "pending": pending}


# ═══════════════════════════════════════════════════════════════
# PID 文件管理
# ═══════════════════════════════════════════════════════════════

def _write_pid():
    PID_FILE.write_text(str(os.getpid()))


def _remove_pid():
    PID_FILE.unlink(missing_ok=True)


def _is_daemon_running() -> bool:
    """检查是否已有 Daemon 在运行。"""
    if not PID_FILE.exists():
        return False
    try:
        pid = int(PID_FILE.read_text().strip())
        if pid <= 0:
            return False
        # Windows 上 os.kill 不可用，改用手动检查
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x0400, False, pid)  # PROCESS_QUERY_INFORMATION
        if handle:
            kernel32.CloseHandle(handle)
            return True
        return False
    except (ValueError, OSError):
        return False


# ═══════════════════════════════════════════════════════════════
# 主循环
# ═══════════════════════════════════════════════════════════════

def _main_loop(sessions_dir: Path, cooldown_minutes: int, poll_interval: int):
    """后台主循环：周期性扫描 + 自动摄入。"""
    logger.info("Hook 捕获引擎已启动")
    logger.info("  监控目录: %s", sessions_dir)
    logger.info("  冷却时间: %d 分钟", cooldown_minutes)
    logger.info("  轮询间隔: %d 秒", poll_interval)

    _write_pid()

    state = _read_hook_state()
    state["running"] = True
    state["pid"] = os.getpid()
    state["started_at"] = datetime.now(timezone.utc).replace(tzinfo=None).isoformat() + "Z"
    state["sessions_scanned"] = 0
    state["sessions_ingested"] = 0
    state["last_scan_at"] = None
    state["errors"] = []
    _write_hook_state(state)

    try:
        while True:
            try:
                # 检查控制命令
                control = _read_hook_control()
                if control.get("command") == "stop":
                    logger.info("收到停止命令，正在退出...")
                    state["running"] = False
                    _write_hook_state(state)
                    _clear_control()
                    break

                # 执行扫描
                result = _scan_and_ingest(sessions_dir, cooldown_minutes, state)
                _write_hook_state(state)

                # 有变更时输出摘要
                if result["ingested"] > 0:
                    logger.info("本轮扫描: 扫描 %d, 摄入 %d, 待处理 %d",
                                 result["scanned"], result["ingested"], result["pending"])

            except Exception as e:
                logger.exception("扫描出错: %s", e)
                state["errors"].append({
                    "error": str(e),
                    "timestamp": datetime.now(timezone.utc).replace(tzinfo=None).isoformat() + "Z",
                })
                _write_hook_state(state)

            time.sleep(poll_interval)

    except KeyboardInterrupt:
        logger.info("收到中断信号，正在退出...")
        state["running"] = False
        _write_hook_state(state)
    finally:
        _remove_pid()
        state["running"] = False
        _write_hook_state(state)


# ═══════════════════════════════════════════════════════════════
# 对外接口（供 Agent 和 MCP 调用）
# ═══════════════════════════════════════════════════════════════

def get_hook_state() -> dict:
    """读取当前 Hook 状态（供外部调用）。"""
    # 如果后台进程未运行，读取最后一次写入的状态
    if not _is_daemon_running():
        state = _read_hook_state()
        state["running"] = False
    else:
        state = _read_hook_state()
    return state


def start_hook_daemon(
    sessions_dir: Optional[str] = None,
    cooldown_minutes: int = COOLDOWN_MINUTES,
    poll_interval: int = POLL_INTERVAL_SECONDS,
) -> dict:
    """启动 Hook 后台守护进程。"""
    if _is_daemon_running():
        return {
            "ok": False,
            "error": "Hook 已在运行中",
            "pid": int(PID_FILE.read_text().strip()) if PID_FILE.exists() else None,
        }

    sd = Path(sessions_dir) if sessions_dir else Path(DEFAULT_SESSIONS_DIR)
    sd = sd / DEFAULT_PROJECT

    # 在子进程中启动主循环
    import subprocess
    import sys

    cmd = [
        sys.executable,
        str(SCRIPT_DIR / "hook_capture.py"),
        "--sessions-dir", str(sd.parent),
        "--project", DEFAULT_PROJECT,
        "--cooldown", str(cooldown_minutes),
        "--interval", str(poll_interval),
        "--daemon",
    ]

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        # 短暂等待确保进程启动
        time.sleep(1.0)
        if proc.poll() is not None:
            stderr = proc.stderr.read().decode("utf-8", errors="replace") if proc.stderr else ""
            return {"ok": False, "error": f"进程启动失败: {stderr}"}

        return {
            "ok": True,
            "pid": proc.pid,
            "message": "Hook 后台守护进程已启动",
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


def stop_hook_daemon() -> dict:
    """停止 Hook 后台守护进程。"""
    if not _is_daemon_running():
        return {"ok": False, "error": "Hook 未在运行"}

    pid = int(PID_FILE.read_text().strip())
    _write_hook_control({"command": "stop"})

    # 等待进程退出
    waited = 0
    while _is_daemon_running() and waited < 10:
        time.sleep(0.5)
        waited += 0.5

    if _is_daemon_running():
        return {"ok": False, "error": f"Hook (PID {pid}) 未能及时停止"}

    _clear_control()
    _remove_pid()
    return {"ok": True, "message": f"Hook (PID {pid}) 已停止"}


def force_ingest_now() -> dict:
    """立即执行一次扫描摄入（不走后台循环）。"""
    from backend import session_ingestor

    sessions_dir = Path(DEFAULT_SESSIONS_DIR) / DEFAULT_PROJECT
    if not sessions_dir.exists():
        return {"ok": False, "error": "会话目录不存在", "scanned": 0, "ingested": 0}

    state = _read_hook_state()
    state.setdefault("ingested_sessions", {})

    result = _scan_and_ingest(sessions_dir, 0, state)
    _write_hook_state(state)

    return {"ok": True, **result}


# ═══════════════════════════════════════════════════════════════
# 入口
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="DevQuest Hook 自动捕获引擎")
    parser.add_argument(
        "--daemon", action="store_true",
        help="以守护进程模式运行（后台）",
    )
    parser.add_argument(
        "--sessions-dir",
        default=str(Path(DEFAULT_SESSIONS_DIR).parent),
        help="会话根目录",
    )
    parser.add_argument(
        "--project", default=DEFAULT_PROJECT,
        help="监控的项目名称",
    )
    parser.add_argument(
        "--cooldown", type=int, default=COOLDOWN_MINUTES,
        help=f"冷却时间（分钟），默认 {COOLDOWN_MINUTES}",
    )
    parser.add_argument(
        "--interval", type=int, default=POLL_INTERVAL_SECONDS,
        help=f"轮询间隔（秒），默认 {POLL_INTERVAL_SECONDS}",
    )

    args = parser.parse_args()

    sessions_dir = Path(args.sessions_dir) / args.project

    if args.daemon:
        # 后台守护进程模式
        if _is_daemon_running():
            logger.error("Hook 已在运行中 (PID: %s)", PID_FILE.read_text().strip())
            sys.exit(1)
        _main_loop(sessions_dir, args.cooldown, args.interval)
    else:
        # 前台单次运行（手动触发）
        logger.info("前台模式: 单次扫描 %s", sessions_dir)
        state = _read_hook_state()
        state["running"] = True
        state.setdefault("ingested_sessions", {})
        state["pending_sessions"] = 0
        _write_hook_state(state)

        try:
            result = _scan_and_ingest(sessions_dir, args.cooldown, state)
            _write_hook_state(state)
            logger.info("扫描完成: %s", json.dumps(result, ensure_ascii=False))
        finally:
            state["running"] = False
            _write_hook_state(state)
