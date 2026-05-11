# -*- coding: utf-8 -*-
"""
DevQuest Log — FastAPI 后端服务

整合所有模块，提供 REST API：
- POST /extract        对话提取 + 分类 + 评分 + 向量索引
- GET  /problems       问题列表（支持筛选）
- GET  /star/{id}      STAR 故事生成
- PUT  /problem/{id}/score  手动更新评分
- GET  /dashboard      统计摘要
- GET  /search         语义搜索
- POST /rebuild-index  重建向量索引
"""

from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from backend.database import init_db, SessionLocal
from backend.models import Project, Problem
from backend import extractor, classifier, scorer, star_gen, vector_search

# ── 启动时初始化 ────────────────────────────────────────────────
init_db()

# 确保 ChromaDB 持久化目录存在
chroma_dir = Path(__file__).resolve().parent.parent / "data" / "chroma_db"
chroma_dir.mkdir(parents=True, exist_ok=True)

# ── FastAPI 应用 ────────────────────────────────────────────────
app = FastAPI(
    title="DevQuest Log API",
    description="开发者项目经验管理与智能复盘系统",
    version="1.0.0",
)

# CORS 中间件 — 允许 Streamlit 前端跨域
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── 全局异常处理 ────────────────────────────────────────────────

@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    """兜底异常处理，避免 500 泄露内部细节。"""
    return {
        "detail": str(exc),
        "error_type": type(exc).__name__,
    }


# ══════════════════════════════════════════════════════════════════
# Pydantic 模型
# ══════════════════════════════════════════════════════════════════

class ExtractRequest(BaseModel):
    conversation_text: str = Field(..., description="AI 编程对话完整文本")
    project_name: str = Field(..., description="所属项目名称")


class ScoreUpdateRequest(BaseModel):
    priority_score: int = Field(..., ge=1, le=10, description="优先级评分 1-10")


# ══════════════════════════════════════════════════════════════════
# API 端点
# ══════════════════════════════════════════════════════════════════

@app.post("/extract")
def api_extract(req: ExtractRequest):
    """
    接收对话文本和项目名，执行完整流水线：
    extractor → classifier → scorer → vector_search

    每步失败独立回退，不会因为单步失败导致整个入库中断。
    """
    # ── Step 1: 提取问题（必须成功，否则返回错误）─────────────────
    try:
        problems = extractor.extract_problems(
            conversation_text=req.conversation_text,
            project_name=req.project_name,
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"问题提取失败: {e}",
        )

    if not problems:
        return {
            "project": req.project_name,
            "count": 0,
            "problems": [],
            "message": "对话中未识别到技术问题",
        }

    # ── Step 2-4: 逐条增强（每步独立，失败不影响其他步骤）───────
    db = SessionLocal()
    try:
        for p in problems:
            pid = p["id"]
            problem = db.query(Problem).filter_by(id=pid).first()
            if not problem:
                continue

            # Step 2: 分类增强（回退：保留提取器原始标签）
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
                pass  # 保留原始分类

            # Step 3: 评分增强（回退：保留默认 5 分）
            try:
                scores = scorer.score_problem(
                    title=p.get("title", ""),
                    description=p.get("description", ""),
                    attempts=p.get("attempts", ""),
                    solution=p.get("solution", ""),
                )
                problem.priority_score = scores.get("total", 5)
            except Exception:
                pass  # 保留默认 5 分

        db.commit()
    finally:
        db.close()

    # Step 4: 向量索引（失败不影响 API 响应）
    index_ok = 0
    for p in problems:
        try:
            if vector_search.add_to_index(p["id"]):
                index_ok += 1
        except Exception:
            pass

    # 返回最终数据
    return {
        "project": req.project_name,
        "count": len(problems),
        "indexed": index_ok,
        "problems": problems,
    }


@app.get("/problems")
def api_problems(
    project: Optional[str] = Query(None, description="按项目名筛选"),
    tech: Optional[str] = Query(None, description="按技术栈关键词筛选"),
    min_score: Optional[int] = Query(None, description="最低优先级分数"),
    problem_type: Optional[str] = Query(None, description="问题类型: Bug/性能优化/架构决策/环境配置/API调试"),
    limit: int = Query(50, ge=1, le=500, description="返回数量上限"),
    offset: int = Query(0, ge=0, description="分页偏移"),
):
    """
    按条件筛选问题列表，支持项目/技术栈/分数/类型组合筛选。
    """
    db = SessionLocal()
    try:
        query = db.query(Problem)

        # 按项目筛选
        if project:
            proj = db.query(Project).filter_by(name=project).first()
            if proj:
                query = query.filter_by(project_id=proj.id)
            else:
                return {"total": 0, "problems": []}

        # 按技术栈筛选（包含匹配）
        if tech:
            query = query.filter(Problem.tech_stack.contains(tech))

        # 按最低分数筛选
        if min_score is not None:
            query = query.filter(Problem.priority_score >= min_score)

        # 按问题类型筛选
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


@app.get("/star/{problem_id}")
def api_star(problem_id: int):
    """
    为指定问题生成 STAR 故事。

    返回 situation / task / action / result 四个字段。
    如已生成过则直接返回缓存。
    """
    db = SessionLocal()
    try:
        problem = db.query(Problem).filter_by(id=problem_id).first()
        if not problem:
            raise HTTPException(status_code=404, detail="问题不存在")

        # 已缓存则直接返回
        if problem.star_story:
            import json
            return {
                "problem_id": problem_id,
                "title": problem.title,
                "star": json.loads(problem.star_story),
                "cached": True,
            }

        # 调用生成模块
        result = star_gen.generate_star(problem_id)
        if not result:
            raise HTTPException(status_code=500, detail="STAR 故事生成失败")

        return {
            "problem_id": problem_id,
            "title": problem.title,
            "star": result,
            "cached": False,
        }
    finally:
        db.close()


@app.put("/problem/{problem_id}/score")
def api_update_score(problem_id: int, req: ScoreUpdateRequest):
    """
    手动更新指定问题的优先级评分。
    """
    db = SessionLocal()
    try:
        problem = db.query(Problem).filter_by(id=problem_id).first()
        if not problem:
            raise HTTPException(status_code=404, detail="问题不存在")

        old_score = problem.priority_score
        problem.priority_score = req.priority_score
        db.commit()

        return {
            "problem_id": problem_id,
            "old_score": old_score,
            "new_score": req.priority_score,
            "message": "评分已更新",
        }
    finally:
        db.close()


@app.get("/dashboard")
def api_dashboard(project: Optional[str] = Query(None)):
    """
    返回统计摘要：总问题数、按类型分布、按分数分布、按项目分布。
    可选按 project 参数筛选单个项目。
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
                "total": 0,
                "by_type": {},
                "by_score_range": {},
                "top_tech": [],
                "avg_score": 0,
            }

        # 按类型分布
        by_type = {}
        for p in problems:
            pt = p.problem_type or "未分类"
            by_type[pt] = by_type.get(pt, 0) + 1

        # 按分数段分布
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

        # 热门技术栈 Top 10
        tech_counts = {}
        for p in problems:
            if p.tech_stack:
                for t in p.tech_stack.split(","):
                    t = t.strip().lower()
                    if t:
                        tech_counts[t] = tech_counts.get(t, 0) + 1
        top_tech = sorted(tech_counts.items(), key=lambda x: x[1], reverse=True)[:10]

        # 平均分
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


@app.get("/search")
def api_search(
    q: str = Query(..., description="查询文本（自然语言描述）"),
    k: int = Query(5, ge=1, le=50, description="返回数量"),
    tech: Optional[str] = Query(None, description="按技术栈过滤"),
    project: Optional[str] = Query(None, description="限定项目范围（知识域收缩）"),
):
    """
    双通道混合搜索（向量 + FTS5 关键词 → RRF 融合）。
    参数 q 为查询文本，k 控制返回数量，tech 可选过滤技术栈，
    project 可选限定项目范围。
    """
    try:
        data = vector_search.search(
            query_text=q, k=k, tech_filter=tech, project_name=project,
        )
        return {
            "query": q,
            "filters": {"tech": tech, "project": project},
            "count": len(data["results"]),
            "results": data["results"],
            "_debug": data.get("_debug"),
        }
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"搜索失败: {e}",
        )


@app.post("/rebuild-index")
def api_rebuild_index():
    """
    从 SQLite 数据库全量重建 ChromaDB 向量索引。
    适用于索引损坏或数据迁移后的恢复场景。
    """
    try:
        result = vector_search.rebuild_index()
        return {
            "message": "索引重建完成",
            "indexed": result["indexed"],
            "errors": result["errors"],
        }
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"索引重建失败: {e}",
        )


@app.get("/health")
def api_health():
    """健康检查端点。"""
    return {
        "status": "ok",
        "version": "1.0.0",
        "services": {
            "database": "sqlite",
            "vector_db": "chromadb",
            "llm_model": __import__("os").getenv("DEEPSEEK_MODEL", "deepseek-v4-flash"),
            "embedding_model": __import__("os").getenv("EMBEDDING_MODEL", "text-embedding-v3"),
        },
    }
