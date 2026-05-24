# -*- coding: utf-8 -*-
"""
DevQuest — MCP Server

基于 Python MCP SDK，将经验库能力以标准 MCP 协议暴露给 MCP Client。
替代原有 FastAPI + Streamlit 架构。

启动方式:
  python backend/mcp_server.py          # stdio 模式 (MCP Client)
  python backend/mcp_server.py --http   # HTTP 模式 (开发调试)
"""

import logging
import json
import os
import sys
from pathlib import Path
from typing import Optional

# 确保项目根目录在 path 中
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from mcp.server.fastmcp import FastMCP

from backend.database import init_db, SessionLocal
from backend.models import Problem, Project
from backend import extractor, classifier, scorer, star_gen, vector_search, session_ingestor, rule_maker, feishu
from backend import services
from backend.agent.harness import HarnessAgent
from backend import llm_client

# ── 启动初始化 ──────────────────────────────────────────────────
init_db()

# 确保 ChromaDB 持久化目录存在
chroma_dir = Path(__file__).resolve().parent.parent / "data" / "chroma_db"
chroma_dir.mkdir(parents=True, exist_ok=True)
DATA_DIR = chroma_dir.parent

# ── Hook 守护进程自动启动 ─────────────────────────────────────────
_scripts_dir = Path(__file__).resolve().parent.parent / "scripts"
if str(_scripts_dir) not in sys.path:
    sys.path.insert(0, str(_scripts_dir))
try:
    import hook_capture
    if not hook_capture._is_daemon_running():
        hook_capture.start_hook_daemon()
        logging.getLogger(__name__).info("Hook 守护进程已自动启动")
except ImportError:
    hook_capture = None
    logging.getLogger(__name__).warning("hook_capture 模块未找到，请检查 scripts/ 目录")
except Exception as e:
    hook_capture = None
    logging.getLogger(__name__).warning("Hook 自动启动失败: %s", e)

# ── MCP Server ──────────────────────────────────────────────────
mcp = FastMCP(
    "DevQuest",
    instructions="开发者外脑 Agent — 自动从 Claude 对话中沉淀技术经验，双通道混合检索历史方案。提供语义搜索、session 摄入、问题管理、统计概览、STAR 故事生成等功能。",
)


# ══════════════════════════════════════════════════════════════════
# 内部辅助函数
# ══════════════════════════════════════════════════════════════════

def _run_extract_pipeline(conversation_text: str, project_name: str) -> dict:
    """
    完整提取流水线：extractor → classifier → scorer → 双通道索引。
    每步独立 try/except，失败不影响后续步骤。
    """
    # Step 1: 提取问题（必须成功）
    problems = extractor.extract_problems(
        conversation_text=conversation_text,
        project_name=project_name,
    )
    if not problems:
        return {"count": 0, "problems": [], "indexed": 0}

    # Step 2-4: 逐条增强
    db = SessionLocal()
    try:
        for p in problems:
            problem = db.query(Problem).filter_by(id=p["id"]).first()
            if not problem:
                continue

            # 分类增强（回退：保留提取器原始标签）
            try:
                new_ts, new_pt = classifier.classify_problem(
                    title=p.get("title", ""),
                    description=p.get("description", ""),
                    solution=p.get("solution", ""),
                )
                if new_ts:
                    problem.tech_stack = new_ts
                if new_pt:
                    problem.problem_type = new_pt
            except Exception:
                pass

            # 评分增强（回退：保留默认 5 分）
            try:
                scores = scorer.score_problem(
                    title=p.get("title", ""),
                    description=p.get("description", ""),
                    attempts=p.get("attempts", ""),
                    solution=p.get("solution", ""),
                )
                problem.priority_score = scores.get("total", 5)
            except Exception:
                pass

        db.commit()
    finally:
        db.close()

    # 向量索引（失败不影响返回）
    index_ok = sum(
        1 for p in problems
        if _safe_index(p["id"])
    )

    return {
        "count": len(problems),
        "problems": problems,
        "indexed": index_ok,
    }


def _safe_index(problem_id: int) -> bool:
    try:
        return vector_search.add_to_index(problem_id)
    except Exception:
        logging.exception("索引失败 problem_id=%s", problem_id)
        return False


# ══════════════════════════════════════════════════════════════════
# MCP Tools
# ══════════════════════════════════════════════════════════════════

@mcp.tool()
def search_experience(
    q: str,
    k: int = 5,
    tech: Optional[str] = None,
    project: Optional[str] = None,
    environment: Optional[dict] = None,
) -> dict:
    """
    双通道混合检索历史技术经验。

    用自然语言描述你遇到的问题，从经验库中检索最相似的解决方案。
    向量语义 + FTS5 关键词双通道检索，RRF 融合排序。
    支持环境过滤：传 environment={"os":"win11"} 会提升匹配经验的权重，不匹配的降权但不排除。

    参数:
        q: 查询文本（自然语言描述）
        k: 返回数量，默认 5
        tech: 按技术栈过滤，如 "Python"、"Docker"
        project: 限定项目范围
        environment: 当前运行环境 dict，如 {"os":"win11","python":"3.12"}
    """
    data = vector_search.search(
        query_text=q, k=k, tech_filter=tech, project_name=project,
        environment=environment,
    )
    # 记录搜索结果曝光（隐式反馈）
    result_ids = [r["problem_id"] for r in data["results"]]
    vector_search.record_search_impressions(result_ids)

    return {
        "query": q,
        "rewritten": data.get("_debug", {}).get("rewritten_query", q),
        "count": len(data["results"]),
        "results": data["results"],
    }


@mcp.tool()
def ingest_sessions(mode: str = "incremental") -> dict:
    """
    从 Claude JSONL 会话文件中自动摄入技术问题。

    扫描 ~/.claude/projects/ 目录，自动识别已结束的对话，
    提取技术问题并入库。

    参数:
        mode: 'incremental' 增量（只处理新会话），'full' 全量重建
    """
    if mode == "full":
        result = session_ingestor.ingest_all()
    else:
        result = session_ingestor.ingest_incremental()
    return {"mode": mode, **result}


@mcp.tool()
def ingest_status() -> dict:
    """查看 session 自动摄入的当前状态：已处理会话数、累计问题数、待处理数。"""
    return session_ingestor.get_ingest_status()


@mcp.tool()
def extract_from_text(conversation_text: str, project_name: str) -> dict:
    """
    手动粘贴一段 AI 编程对话，提取其中的技术问题并入库。

    用于处理非 Claude 对话（如 ChatGPT、Copilot Chat 等），
    或处理无法自动摄入的历史对话。

    参数:
        conversation_text: 完整的 AI 编程对话文本
        project_name: 所属项目名称
    """
    result = _run_extract_pipeline(conversation_text, project_name)
    return {
        "project": project_name,
        **result,
    }


@mcp.tool()
def save_problem(
    error: str,
    solution: str,
    attempts: Optional[list] = None,
    environment: Optional[dict] = None,
    project: Optional[str] = None,
    problem_type: Optional[str] = None,
    tech_stack: Optional[list] = None,
) -> dict:
    """
    结构化录入一个问题，跳过 LLM 提取步骤。适合已明确知道错误+解法的场景。

    参数:
        error: 错误描述或复现步骤
        solution: 最终解决方案
        attempts: 尝试过的方案列表，如 ["方案1","方案2"]
        environment: 运行环境，如 {"os":"win11","python":"3.12","docker":"26.1"}
        project: 项目名，不传默认 "Unknown"
        problem_type: 问题类型，不传则自动分类
        tech_stack: 技术栈列表，不传则自动分类
    """
    result = services.save_problem_service(
        error=error,
        solution=solution,
        attempts=attempts,
        environment=environment,
        project=project,
        problem_type=problem_type,
        tech_stack=tech_stack,
    )
    return result


@mcp.tool()
def record_feedback(problem_id: int, helpful: bool, note: Optional[str] = None) -> dict:
    """
    记录用户对某条经验的反馈，影响后续搜索排序。

    参数:
        problem_id: 问题 ID
        helpful: True 表示有用，False 表示没用
        note: 可选，备注为什么有用/没用
    """
    return services.record_feedback_service(problem_id, helpful, note)


@mcp.tool()
def list_problems(
    project: Optional[str] = None,
    tech: Optional[str] = None,
    min_score: Optional[int] = None,
    problem_type: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """
    按条件筛选问题列表，支持项目/技术栈/评分/类型组合筛选。

    参数:
        project: 按项目名筛选，如 "电商后台管理系统"
        tech: 按技术栈关键词筛选，如 "Docker"
        min_score: 最低优先级评分 (1-10)
        problem_type: 问题类型 — Bug/性能优化/架构决策/环境配置/API调试
        limit: 返回数量上限，默认 50
        offset: 分页偏移
    """
    db = SessionLocal()
    try:
        query = db.query(Problem)

        if project:
            proj = db.query(Project).filter_by(name=project).first()
            if proj:
                query = query.filter_by(project_id=proj.id)
            else:
                return {"total": 0, "problems": []}

        if tech:
            query = query.filter(Problem.tech_stack.contains(tech))
        if min_score is not None:
            query = query.filter(Problem.priority_score >= min_score)
        if problem_type:
            query = query.filter_by(problem_type=problem_type)

        total = query.count()
        problems = (
            query
            .order_by(Problem.priority_score.desc(), Problem.created_at.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )
        return {
            "total": total,
            "limit": limit,
            "offset": offset,
            "problems": [p.to_dict() for p in problems],
        }
    finally:
        db.close()


@mcp.tool()
def get_dashboard(project: Optional[str] = None) -> dict:
    """
    返回经验库统计摘要：总问题数、按类型/分数/技术栈分布、平均评分。

    参数:
        project: 可选，限定统计范围到指定项目
    """
    db = SessionLocal()
    try:
        query = db.query(Problem)
        if project:
            proj = db.query(Project).filter_by(name=project).first()
            if not proj:
                return {"message": "项目不存在", "project": project}
            query = query.filter_by(project_id=proj.id)

        problems = query.all()
        total = len(problems)
        if total == 0:
            return {
                "total": 0, "by_type": {}, "by_score_range": {},
                "top_tech": [], "avg_score": 0,
            }

        by_type = {}
        for p in problems:
            pt = p.problem_type or "未分类"
            by_type[pt] = by_type.get(pt, 0) + 1

        by_score_range = {"1-3": 0, "4-6": 0, "7-8": 0, "9-10": 0}
        for p in problems:
            s = p.priority_score or 5
            if s <= 3:
                by_score_range["1-3"] += 1
            elif s <= 6:
                by_score_range["4-6"] += 1
            elif s <= 8:
                by_score_range["7-8"] += 1
            else:
                by_score_range["9-10"] += 1

        tech_counts = {}
        for p in problems:
            if p.tech_stack:
                for t in p.tech_stack.split(","):
                    t = t.strip().lower()
                    if t:
                        tech_counts[t] = tech_counts.get(t, 0) + 1
        top_tech = sorted(tech_counts.items(), key=lambda x: x[1], reverse=True)[:10]

        avg_score = round(sum(p.priority_score or 0 for p in problems) / total, 1)

        return {
            "total": total,
            "by_type": by_type,
            "by_score_range": by_score_range,
            "top_tech": [{"tech": t, "count": c} for t, c in top_tech],
            "avg_score": avg_score,
        }
    finally:
        db.close()


@mcp.tool()
def rebuild_index() -> dict:
    """从 SQLite 数据库全量重建 ChromaDB 向量索引和 FTS5 全文索引。"""
    result = vector_search.rebuild_index()
    return {"message": "索引重建完成", **result}


@mcp.tool()
def generate_star(problem_id: int) -> dict:
    """
    为指定问题生成 STAR 面试故事（Situation / Task / Action / Result）。

    参数:
        problem_id: 问题 ID
    """
    import json

    db = SessionLocal()
    try:
        problem = db.query(Problem).filter_by(id=problem_id).first()
        if not problem:
            return {"error": "问题不存在"}

        if problem.star_story:
            vector_search.record_usage(problem_id)
            return {
                "problem_id": problem_id,
                "title": problem.title,
                "star": json.loads(problem.star_story),
                "cached": True,
            }

        result = star_gen.generate_star(problem_id)
        if not result:
            return {"error": "STAR 故事生成失败"}

        vector_search.record_usage(problem_id)
        return {
            "problem_id": problem_id,
            "title": problem.title,
            "star": result,
            "cached": False,
        }
    finally:
        db.close()


@mcp.tool()
def update_score(problem_id: int, score: int) -> dict:
    """
    手动更新指定问题的优先级评分。

    参数:
        problem_id: 问题 ID
        score: 新评分 (1-10)
    """
    if not 1 <= score <= 10:
        return {"error": "评分必须在 1-10 之间"}

    db = SessionLocal()
    try:
        problem = db.query(Problem).filter_by(id=problem_id).first()
        if not problem:
            return {"error": "问题不存在"}

        old_score = problem.priority_score
        problem.priority_score = score
        db.commit()
        return {
            "problem_id": problem_id,
            "old_score": old_score,
            "new_score": score,
        }
    finally:
        db.close()


@mcp.tool()
def run_reflection() -> dict:
    """
    触发 Rule-Maker 反思引擎。

    读取本周新增的技术问题，LLM 分析共性模式，
    生成平台无关的规则草案，写入 rules_suggestions.md。

    规则不直接覆写项目规则文件——需要人工 review 确认（Human-in-the-loop）。
    """
    result = rule_maker.run_reflection()
    return result


@mcp.tool()
def get_suggestions() -> dict:
    """
    查看当前待确认的规则建议草案。

    返回 rules_suggestions.md 的内容，
    包含 LLM 生成的规则、置信度、来源问题。
    """
    result = rule_maker.get_suggestions()
    return result


@mcp.tool()
def push_feishu_weekly() -> dict:
    """
    将本周经验摘要推送到飞书群。

    通过飞书自定义机器人 Webhook 发送卡片消息，
    包含本周新增问题数、类型分布、Top 5 问题。
    需要 .env 中配置 FEISHU_WEBHOOK_URL。
    """
    import os
    from pathlib import Path as _Path

    webhook_url = os.getenv("FEISHU_WEBHOOK_URL", "")
    if not webhook_url:
        return {"ok": False, "error": "未配置 FEISHU_WEBHOOK_URL"}

    # 先跑 Rule-Maker 反思（如果本周有问题）
    try:
        rule_maker.run_reflection()
    except Exception:
        pass

    # 推送周报
    result = feishu.push_weekly_summary(webhook_url)

    # 如果有规则草案，追加知识推送
    suggestions = rule_maker.get_suggestions()
    if suggestions.get("exists") and result.get("problem_count", 0) >= 3:
        content = suggestions["content"]
        # 提取摘要和规则数量
        lines = content.split("\n")
        summary = next((l.strip("## ") for l in lines if "模式摘要" in l), "")
        # 推知识卡片
        feishu.send_card(
            webhook_url,
            "🧠 DevQuest 团队知识推送",
            f"本周 Reflections 已完成，{result['problem_count']} 个新问题。\n\n📎 详情见 `rules_suggestions.md`",
            template="purple",
        )

    return result


@mcp.tool()
def run_agent() -> dict:
    """
    手动触发 DevQuest Agent 执行一次认知循环。

    Agent 自主观察系统状态，决定当前最值得做的事并执行。
    返回 Agent 的决策、执行结果和当前系统状态摘要。
    """
    agent = HarnessAgent()
    return agent.run()


@mcp.tool()
def llm_status() -> dict:
    """
    查看 LLM 提供商状态和额度通知。

    返回 Primary/Fallback 两个提供商的可用性，
    以及是否有待处理的额度耗尽通知需要用户确认。
    当 go 额度用完后，此 tool 会显示通知，用户可通过
    acknowledge_quota 确认是否切换至直连 Fallback。
    """
    status = llm_client.get_llm_status()

    notification = llm_client.get_quota_notification()
    if notification:
        status["action_required"] = "额度耗尽通知待确认"
        status["how_to_resolve"] = (
            "调用 acknowledge_quota(continue_fallback=True) 同意切换至直连 DeepSeek API，"
            "或 acknowledge_quota(continue_fallback=False) 拒绝并等待 Primary 恢复"
        )

    return status


@mcp.tool()
def acknowledge_quota(continue_fallback: bool = True) -> dict:
    """
    确认 LLM 额度耗尽通知。

    当 go（opencode.ai）额度用完后，系统会暂停自动切换并发出通知。
    调用此 tool 确认你的决策：

    - continue_fallback=True: 同意切换至直连 DeepSeek API（Fallback）
    - continue_fallback=False: 不同意，等待 Primary 恢复后再用

    参数:
        continue_fallback: 是否使用 Fallback，默认 True
    """
    notification = llm_client.get_quota_notification()
    if notification is None:
        return {"message": "当前无待处理的额度通知", "status": "idle"}

    llm_client.acknowledge_quota(continue_fallback)
    return {
        "acknowledged": True,
        "decision": "使用 Fallback" if continue_fallback else "等待 Primary 恢复",
        "current_status": llm_client.get_llm_status(),
    }


# ══════════════════════════════════════════════════════════════════
# V4.2 — Hook 自动捕获工具
# ══════════════════════════════════════════════════════════════════

@mcp.tool()
def hook_status() -> dict:
    """
    查看 Hook 自动捕获引擎的运行状态。

    返回 Hook 是否在后台运行、已摄入会话数、待处理会话数、
    DAG 上下文摘要等。

    零操作目标的核心：Hook 在后台自动监听会话结束并入库。
    """
    if hook_capture is None:
        return {"error": "hook_capture 模块未加载", "running": False}

    state = hook_capture.get_hook_state()
    return {
        "running": state.get("running", False),
        "pid": state.get("pid"),
        "started_at": state.get("started_at"),
        "last_scan_at": state.get("last_scan_at"),
        "sessions_scanned": state.get("sessions_scanned", 0),
        "sessions_ingested": state.get("sessions_ingested", 0),
        "pending_sessions": state.get("pending_sessions", 0),
        "last_ingested_session": state.get("last_ingested_session"),
        "dag_context_summary": _summarize_dag_for_tool(state.get("dag_context", {})),
        "errors": state.get("errors", [])[-5:],
    }


@mcp.tool()
def start_hook() -> dict:
    """
    启动 Hook 自动捕获引擎（后台守护进程）。

    Hook 启动后会持续监控 Claude 会话文件，
    检测到会话结束后自动触发经验摄入。
    状态变更通过数据文件（data/hook_state.json）供 Agent 和 MCP 读取。

    零操作：启动后无需任何手动操作，全自动运行。
    """
    if hook_capture is None:
        return {"ok": False, "error": "hook_capture 模块未加载"}

    result = hook_capture.start_hook_daemon()
    return result


@mcp.tool()
def stop_hook() -> dict:
    """
    停止 Hook 自动捕获引擎。

    发送停止信号给后台守护进程，等待其安全退出。
    """
    if hook_capture is None:
        return {"ok": False, "error": "hook_capture 模块未加载"}

    result = hook_capture.stop_hook_daemon()
    return result


@mcp.tool()
def get_dag_context() -> dict:
    """
    查看 DAG（有向无环图）上下文。

    返回从会话中收集的工作目录、git 分支、文件变更关系等信息。
    用于增强搜索上下文和知识关联。
    """
    if hook_capture is None:
        state = _read_hook_state_direct()
    else:
        state = hook_capture.get_hook_state()

    dag = state.get("dag_context", {})
    return _format_dag_for_tool(dag)


def _read_hook_state_direct() -> dict:
    """直接读取 hook_state.json（无需 hook_capture 模块）。"""
    try:
        state_file = DATA_DIR / "hook_state.json"
        if state_file.exists():
            return json.loads(state_file.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _summarize_dag_for_tool(dag: dict) -> dict:
    """为 hook_status 压缩 DAG 摘要。"""
    sessions = dag.get("sessions", {})
    cwds = set()
    branches = set()
    for s in sessions.values():
        if s.get("cwd"):
            cwds.add(s["cwd"])
        for b in s.get("git_branches", []):
            branches.add(b)
    return {
        "tracked_sessions": len(sessions),
        "working_directories": sorted(cwds),
        "git_branches": sorted(branches),
    }


def _format_dag_for_tool(dag: dict) -> dict:
    """为 get_dag_context 展开 DAG。"""
    sessions = dag.get("sessions", {})
    return {
        "total_tracked_sessions": len(sessions),
        "sessions": {k: v for k, v in list(sessions.items())[-20:]},
    }


# ══════════════════════════════════════════════════════════════════
# 入口
# ══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="DevQuest MCP Server")
    parser.add_argument(
        "--http",
        action="store_true",
        help="以 HTTP 模式启动（开发调试用），默认 stdio 模式",
    )
    parser.add_argument(
        "--port", type=int, default=8000,
        help="HTTP 模式端口号（默认 8000）",
    )
    args = parser.parse_args()

    if args.http:
        mcp.settings.port = args.port
        mcp.run(transport="streamable-http")
    else:
        mcp.run(transport="stdio")
