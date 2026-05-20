# -*- coding: utf-8 -*-
"""
DevQuest — 双通道混合检索模块

双通道检索架构:
- 向量通道: ChromaDB 语义检索（余弦距离）
- 关键词通道: SQLite FTS5 全文索引（BM25 排序）
- RRF 融合: Reciprocal Rank Fusion (k=60)

支持:
- 按项目范围裁剪（知识域收缩）
- _debug 影子观测：返回每通道原始排名与融合权重
"""

import json
import logging
import os
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ── 项目根目录 ────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent

from dotenv import load_dotenv
load_dotenv(BASE_DIR / ".env")

import chromadb
from openai import OpenAI

from backend.database import SessionLocal, engine
from backend.models import Problem, Project

# ── 阿里百炼 Embedding 配置 ────────────────────────────────────
EMBEDDING_API_KEY = os.getenv("EMBEDDING_API_KEY", "")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-v3")
EMBEDDING_BASE_URL = os.getenv(
    "EMBEDDING_BASE_URL",
    "https://dashscope.aliyuncs.com/compatible-mode/v1",
)

# ── ChromaDB 持久化路径 ────────────────────────────────────────
CHROMA_DIR = BASE_DIR / "data" / "chroma_db"
CHROMA_DIR.mkdir(parents=True, exist_ok=True)

COLLECTION_NAME = "problems"
RRF_K = 60  # RRF 融合常数

# ── 全局客户端（延迟初始化）─────────────────────────────────────
_client: Optional[chromadb.PersistentClient] = None
_embed_client: Optional[OpenAI] = None
_collection: Optional[chromadb.Collection] = None


def _get_embed_client() -> OpenAI:
    global _embed_client
    if _embed_client is None:
        _embed_client = OpenAI(
            api_key=EMBEDDING_API_KEY,
            base_url=EMBEDDING_BASE_URL,
        )
    return _embed_client


def _get_collection() -> chromadb.Collection:
    global _client, _collection
    if _collection is None:
        _client = chromadb.PersistentClient(path=str(CHROMA_DIR))
        _collection = _client.get_or_create_collection(
            name=COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )
    return _collection


def _embed(text: str) -> list[float]:
    client = _get_embed_client()
    response = client.embeddings.create(model=EMBEDDING_MODEL, input=text)
    return response.data[0].embedding


def _embed_batch(texts: list[str]) -> list[list[float]]:
    """批量 embedding，阿里百炼限制单次最多 10 条。"""
    client = _get_embed_client()
    results = []
    batch_size = 10
    for i in range(0, len(texts), batch_size):
        chunk = texts[i:i + batch_size]
        resp = client.embeddings.create(model=EMBEDDING_MODEL, input=chunk)
        results.extend(d.embedding for d in resp.data)
    return results


# ── FTS5 全文索引操作 ──────────────────────────────────────────

def _sync_fts(problem_id: int, title: str, description: str, solution: str):
    """同步单条问题到 FTS5 全文索引（upsert 语义）。"""
    with engine.connect() as conn:
        from sqlalchemy import text
        # 先删后插，实现 upsert
        conn.execute(text("DELETE FROM problems_fts WHERE rowid = :id"), {"id": problem_id})
        conn.execute(
            text("INSERT INTO problems_fts(rowid, title, description, solution) "
                 "VALUES (:id, :title, :desc, :sol)"),
            {"id": problem_id, "title": title, "desc": description, "sol": solution},
        )
        conn.commit()


def _delete_fts(problem_id: int):
    """从 FTS5 索引中删除指定问题。"""
    with engine.connect() as conn:
        from sqlalchemy import text
        conn.execute(text("DELETE FROM problems_fts WHERE rowid = :id"), {"id": problem_id})
        conn.commit()


def _rebuild_fts():
    """从 problems 表全量重建 FTS5 全文索引。"""
    db = SessionLocal()
    try:
        problems = db.query(Problem).all()
        with engine.connect() as conn:
            from sqlalchemy import text
            conn.execute(text("DELETE FROM problems_fts"))
            for p in problems:
                conn.execute(
                    text("INSERT INTO problems_fts(rowid, title, description, solution) "
                         "VALUES (:id, :title, :desc, :sol)"),
                    {"id": p.id, "title": p.title or "", "desc": p.description or "",
                     "sol": p.solution or ""},
                )
            conn.commit()
    finally:
        db.close()


# ── 文档拼接 ───────────────────────────────────────────────────

def _build_document(problem: Problem) -> str:
    parts = []
    if problem.title:
        parts.append(f"问题：{problem.title}")
    if problem.description:
        parts.append(f"描述：{problem.description}")
    if problem.solution:
        parts.append(f"解决方案：{problem.solution}")
    return "\n".join(parts)


# ── 索引操作 ───────────────────────────────────────────────────

def add_to_index(problem_id: int) -> bool:
    """
    将单个问题同时加入向量索引和 FTS5 全文索引。
    """
    db = SessionLocal()
    try:
        problem = db.query(Problem).filter_by(id=problem_id).first()
        if not problem:
            return False

        doc = _build_document(problem)
        if not doc.strip():
            return False

        # 向量索引
        vector = _embed(doc)
        collection = _get_collection()
        collection.upsert(
            ids=[str(problem.id)],
            embeddings=[vector],
            documents=[doc],
            metadatas=[{
                "problem_id": problem.id,
                "project_id": problem.project_id or 0,
                "title": problem.title or "",
                "tech_stack": problem.tech_stack or "",
                "priority_score": problem.priority_score or 5,
            }],
        )

        # FTS5 全文索引
        _sync_fts(problem.id, problem.title or "", problem.description or "",
                  problem.solution or "")

        return True
    except Exception:
        logger.exception("add_to_index 失败 problem_id=%s", problem_id)
        return False
    finally:
        db.close()


def add_to_index_batch(problem_ids: list[int]) -> int:
    count = 0
    for pid in problem_ids:
        if add_to_index(pid):
            count += 1
    return count


def delete_from_index(problem_id: int) -> bool:
    try:
        collection = _get_collection()
        collection.delete(ids=[str(problem_id)])
        _delete_fts(problem_id)
        return True
    except Exception:
        return False


# ══════════════════════════════════════════════════════════════════
# 隐式反馈
# ══════════════════════════════════════════════════════════════════

def _load_usage_boosts() -> dict[int, int]:
    """加载所有问题的使用次数，用于搜索排序 boost。"""
    db = SessionLocal()
    try:
        from sqlalchemy import text
        rows = db.execute(text(
            "SELECT id, usage_count FROM problems WHERE usage_count > 0"
        )).fetchall()
        return {row[0]: row[1] for row in rows}
    except Exception:
        return {}
    finally:
        db.close()


def _load_meta_for_boost(pids: set[int]) -> dict[int, dict]:
    """批量加载问题的环境和时间信息，用于环境匹配 boost 和时效衰减。"""
    if not pids:
        return {}
    db = SessionLocal()
    try:
        from sqlalchemy import text
        rows = db.execute(text(
            "SELECT id, environment, first_seen_at "
            "FROM problems WHERE id IN ({})".format(
                ",".join(str(i) for i in pids)
            )
        )).fetchall()
        result = {}
        for row in rows:
            pid = row[0]
            env_str = row[1]
            first_seen = row[2]
            if isinstance(first_seen, str):
                try:
                    first_seen = datetime.fromisoformat(first_seen)
                except (ValueError, TypeError):
                    first_seen = None
            env_dict = None
            os_val = None
            if env_str:
                try:
                    env_dict = json.loads(env_str) if isinstance(env_str, str) else env_str
                    os_val = env_dict.get("os") if isinstance(env_dict, dict) else None
                except (json.JSONDecodeError, TypeError):
                    pass
            result[pid] = {
                "environment": env_str,
                "os": os_val,
                "first_seen_at": first_seen,
            }
        return result
    except Exception:
        logger.exception("_load_meta_for_boost 失败")
        return {}
    finally:
        db.close()


def record_usage(problem_id: int):
    """记录一次问题被使用（STAR 生成 / 搜索点击等）。"""
    db = SessionLocal()
    try:
        from sqlalchemy import text
        db.execute(
            text("UPDATE problems SET usage_count = usage_count + 1 WHERE id = :id"),
            {"id": problem_id},
        )
        db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()


def record_search_impressions(problem_ids: list[int]):
    """记录搜索结果曝光，用于未来 CTR 分析和排序优化。"""
    if not problem_ids:
        return
    try:
        from sqlalchemy import text
        db = SessionLocal()
        try:
            pids_str = ",".join(str(i) for i in problem_ids)
            db.execute(text(
                f"UPDATE problems SET usage_count = usage_count + 1 "
                f"WHERE id IN ({pids_str})"
            ))
            db.commit()
        finally:
            db.close()
    except Exception:
        logger.exception("record_search_impressions 失败")


# ══════════════════════════════════════════════════════════════════
# 查询改写
# ══════════════════════════════════════════════════════════════════

# 自然语言中的口语化填充词，会被移除以提高检索精度
_QUERY_FILLER_WORDS = [
    # 中文口语填充
    "上次那个", "我记得有个", "之前遇到过", "帮我查一下", "帮我查查",
    "我想找", "有没有", "怎么修的", "怎么解决的", "那个问题",
    "之前那个", "好像有个", "大概是一个", "记不清了",
    "那个", "这个", "有个", "一种", "怎么", "什么",
    # 英文口语填充
    "help me find", "help me ", "how to ", "how do i ", "i remember ",
    "i think ", "can you ", "please ", "look up ", "search for ",
    "find me ", "what is ", "what are ", "tell me ", "show me ",
]
_QUERY_FILLER_RE = "|".join(_QUERY_FILLER_WORDS)


def _rewrite_query(query_text: str) -> str:
    """
    查询改写：去除口语化填充词，提取技术关键词，提升检索命中率。
    不做 LLM 调用——纯规则引擎，零延迟、零成本。
    """
    import re

    cleaned = query_text.strip()
    # 移除口语填充词（大小写不敏感）
    cleaned = re.sub(_QUERY_FILLER_RE, " ", cleaned, flags=re.IGNORECASE)
    # 合并多余空白
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    # 如果清空了则返回原文
    return cleaned if cleaned else query_text.strip()


# ══════════════════════════════════════════════════════════════════
# 双通道混合检索 + RRF 融合
# ══════════════════════════════════════════════════════════════════

def search(
    query_text: str,
    k: int = 5,
    tech_filter: Optional[str] = None,
    project_name: Optional[str] = None,
    environment: Optional[dict] = None,
) -> dict:
    """
    双通道混合检索：向量语义 + FTS5 关键词 → RRF 融合排序。

    参数:
        query_text: 查询文本
        k: 返回数量
        tech_filter: 技术栈过滤关键词
        project_name: 可选，限定搜索范围到指定项目
        environment: 可选，当前环境 dict，匹配经验权重提升

    返回:
        dict: {"results": [...], "_debug": {...}}
    """
    # ── 查询改写：去口语化，提取技术关键词 ──────────────────
    rewritten = _rewrite_query(query_text)

    # ── 确定项目 ID（知识域收缩）─────────────────────────────
    project_id = None
    if project_name:
        db = SessionLocal()
        try:
            proj = db.query(Project).filter_by(name=project_name).first()
            if proj:
                project_id = proj.id
        finally:
            db.close()

    # ── 通道一: 向量检索 ─────────────────────────────────────
    vector_results = _vector_search(rewritten, k * 3, tech_filter, project_id)

    # ── 通道二: FTS5 关键词检索 ──────────────────────────────
    keyword_results = _keyword_search(rewritten, k * 3, tech_filter, project_id)

    # ── 加载使用计数（隐式反馈 boost）────────────────────────
    usage_boosts = _load_usage_boosts()

    # ── RRF 融合（含环境匹配 boost + 时效衰减）────────────────
    fused = _rrf_fusion(vector_results, keyword_results, k, RRF_K,
                        usage_boosts, environment)

    # ── 补全文档内容 ─────────────────────────────────────────
    results = _enrich_results(fused)

    return {
        "results": results,
        "_debug": {
            "original_query": query_text,
            "rewritten_query": rewritten,
            "vector": vector_results[:k],
            "keyword": keyword_results[:k],
            "fused": fused,
            "rrf_k": RRF_K,
        },
    }


def _vector_search(
    query_text: str,
    fetch_k: int,
    tech_filter: Optional[str] = None,
    project_id: Optional[int] = None,
) -> list[dict]:
    """向量通道：ChromaDB 语义检索。"""
    collection = _get_collection()
    query_vector = _embed(query_text)

    # 构建 ChromaDB where 条件
    where = None
    if project_id is not None:
        where = {"project_id": project_id}
    # 注意: ChromaDB 1.x 不支持 AND 组合 where，technology filter 在 Python 侧处理

    results = collection.query(
        query_embeddings=[query_vector],
        n_results=fetch_k,
        where=where,
        include=["metadatas", "distances"],
    )

    output = []
    if not results["ids"] or not results["ids"][0]:
        return output

    ids = results["ids"][0]
    metas = results["metadatas"][0] if results["metadatas"] else []
    distances = results["distances"][0] if results["distances"] else []

    for i, pid in enumerate(ids):
        item_tech = metas[i].get("tech_stack", "") if i < len(metas) else ""
        # Python 侧按技术栈过滤
        if tech_filter and tech_filter.lower() not in item_tech.lower():
            continue
        output.append({
            "problem_id": int(pid),
            "title": metas[i].get("title", "") if i < len(metas) else "",
            "tech_stack": item_tech,
            "priority_score": metas[i].get("priority_score", 0) if i < len(metas) else 0,
            "distance": distances[i] if i < len(distances) else 0.0,
            "source": "vector",
        })
    return output


def _keyword_search(
    query_text: str,
    fetch_k: int,
    tech_filter: Optional[str] = None,
    project_id: Optional[int] = None,
) -> list[dict]:
    """关键词通道：FTS5（英文/数字） + SQL LIKE（中文兜底）。"""
    from sqlalchemy import text

    db = SessionLocal()
    try:
        fts_query = _build_fts_query(query_text)
        rows = []

        if fts_query != "*":
            # 有英文/数字 token → 走 FTS5
            rows = db.execute(
                text(
                    "SELECT f.rowid, p.title, p.tech_stack, p.priority_score, "
                    "       0.0 - f.rank AS score "
                    "FROM problems_fts f "
                    "JOIN problems p ON f.rowid = p.id "
                    "WHERE problems_fts MATCH :query "
                    + ("AND p.project_id = :proj_id " if project_id is not None else "")
                    + "ORDER BY f.rank "
                    "LIMIT :limit"
                ),
                {
                    "query": fts_query,
                    **({"proj_id": project_id} if project_id is not None else {}),
                    "limit": fetch_k,
                },
            ).fetchall()

        # 中文词 → LIKE 兜底（与 FTS5 结果合并）
        cn_tokens = [t for t in query_text.split() if len(t) > 1 and not t.isascii()]
        if cn_tokens:
            like_conds = " OR ".join(
                "(p.title LIKE :t{i} OR p.description LIKE :t{i} OR p.solution LIKE :t{i})"
                .format(i=i)
                for i in range(len(cn_tokens))
            )
            params = {}
            for i, t in enumerate(cn_tokens):
                params[f"t{i}"] = f"%{t}%"

            sql = (
                "SELECT p.id AS rowid, p.title, p.tech_stack, p.priority_score, "
                "       1.0 AS score "
                "FROM problems p "
                "WHERE (" + like_conds + ")"
                + ("AND p.project_id = :proj_id " if project_id is not None else "")
                + "LIMIT :limit"
            )
            params["limit"] = fetch_k
            if project_id is not None:
                params["proj_id"] = project_id

            like_rows = db.execute(text(sql), params).fetchall()
            # 去重合并：按 rowid 去重，取最高分
            seen = {r.rowid for r in rows}
            for lr in like_rows:
                if lr.rowid not in seen:
                    rows.append(lr)
                    seen.add(lr.rowid)

        # 按 score 降序排列（FTS5 的 -rank 为负值越小越好，LIKE 的 1.0 优先）
        rows = sorted(rows, key=lambda r: r.score, reverse=True)[:fetch_k]
    finally:
        db.close()

    output = []
    for i, row in enumerate(rows):
        item_tech = row.tech_stack or ""
        if tech_filter and tech_filter.lower() not in item_tech.lower():
            continue
        output.append({
            "problem_id": row.rowid,
            "title": row.title or "",
            "tech_stack": item_tech,
            "priority_score": row.priority_score or 5,
            "bm25_rank": i + 1,  # 排名，越小越好
            "source": "keyword",
        })
    return output


def _build_fts_query(query_text: str) -> str:
    """
    将自然语言查询转为 FTS5 查询语法。
    FTS5 默认分词器不支持中文，仅用英文/数字 token 做前缀匹配。
    """
    tokens = [t.strip() for t in query_text.split() if len(t.strip()) > 1]
    if not tokens:
        return "*"

    ascii_tokens = [f'"{t}"*' for t in tokens[:10] if t.isascii()]
    if not ascii_tokens:
        return "*"  # 纯中文查询，FTS5 无法处理，走 LIKE 兜底
    return " OR ".join(ascii_tokens)


def _rrf_fusion(
    vector_results: list[dict],
    keyword_results: list[dict],
    k: int,
    rrf_k: int = 60,
    usage_boosts: dict | None = None,
    environment: dict | None = None,
) -> list[dict]:
    """
    RRF (Reciprocal Rank Fusion) 融合两路检索结果。

    公式: RRF(d) = Σ 1 / (k + rank_i(d))
    分数越高越相关。

    隐式反馈 boost：高频使用的文档获得最多 30% 的额外权重。
    boost = 1 + min(usage_count, 10) * 0.03

    环境匹配 boost：OS 匹配的经验 +15% RRF 权重。

    时效衰减：每过 1 个月权重乘 0.85。
    time_decay = 0.85 ^ months_since_first_seen
    """
    scores: dict[int, float] = defaultdict(float)
    doc_info: dict[int, dict] = {}

    # 向量通道：rank 1 = 距离最小
    for rank, item in enumerate(vector_results, start=1):
        pid = item["problem_id"]
        scores[pid] += 1.0 / (rrf_k + rank)
        if pid not in doc_info:
            doc_info[pid] = item

    # 关键词通道：rank 1 = BM25 分数最高（f.rank 最小）
    for rank, item in enumerate(keyword_results, start=1):
        pid = item["problem_id"]
        scores[pid] += 1.0 / (rrf_k + rank)
        if pid not in doc_info:
            doc_info[pid] = item

    # ── 加载环境信息和时间戳（仅当需要环境匹配或时效衰减时）───
    problem_meta = _load_meta_for_boost(set(scores.keys()))

    # ── 隐式反馈 boost ────────────────────────────────────────
    if usage_boosts:
        for pid, usage in usage_boosts.items():
            if pid in scores and usage > 0:
                boost = 1.0 + min(usage, 10) * 0.03
                scores[pid] *= boost

    # ── 环境匹配 boost + 时效衰减 ─────────────────────────────
    now = datetime.now(timezone.utc)
    for pid in list(scores.keys()):
        meta = problem_meta.get(pid, {})

        # 环境匹配：OS 一致 +15% 权重
        if environment and meta.get("os"):
            if environment.get("os", "").lower() == meta["os"].lower():
                scores[pid] *= 1.15
                doc_info[pid]["env_match"] = True
            else:
                doc_info[pid]["env_match"] = False
        else:
            doc_info[pid]["env_match"] = None

        # 时效衰减：0.85 ^ months
        first_seen = meta.get("first_seen_at")
        if first_seen:
            delta = now - first_seen.replace(tzinfo=timezone.utc)
            months = max(0.0, delta.days / 30.0)
            time_decay = 0.85 ** months
            scores[pid] *= time_decay

    # 按 RRF 分数降序排列
    sorted_ids = sorted(scores, key=lambda x: scores[x], reverse=True)[:k]

    result = []
    for pid in sorted_ids:
        info = doc_info[pid]
        meta = problem_meta.get(pid, {})
        result.append({
            "problem_id": pid,
            "title": info.get("title", ""),
            "tech_stack": info.get("tech_stack", ""),
            "priority_score": info.get("priority_score", 5),
            "rrf_score": round(scores[pid], 6),
            "vector_distance": info.get("distance"),
            "bm25_rank": info.get("bm25_rank"),
            "sources": _get_sources(pid, vector_results, keyword_results),
            "environment": meta.get("environment"),
            "environment_match": info.get("env_match"),
        })
    return result


def _get_sources(pid: int, vector: list[dict], keyword: list[dict]) -> list[str]:
    """判断结果来自哪些通道。"""
    srcs = []
    for item in vector:
        if item["problem_id"] == pid:
            srcs.append("vector")
            break
    for item in keyword:
        if item["problem_id"] == pid:
            srcs.append("keyword")
            break
    return srcs


def _enrich_results(fused: list[dict]) -> list[dict]:
    """为融合结果补全 document 字段（用于前端展示）。"""
    if not fused:
        return []
    db = SessionLocal()
    try:
        ids = [item["problem_id"] for item in fused]
        problems = db.query(Problem).filter(Problem.id.in_(ids)).all()
        id_to_doc = {p.id: _build_document(p) for p in problems}

        for item in fused:
            pid = item["problem_id"]
            item["document"] = id_to_doc.get(pid, "")
            # 将 RRF 分数映射为 0-1 距离，兼容前端 (1 - distance) 计算
            # RRF 通常 0.01-0.05，映射到 0-0.5 的"距离"
            item["distance"] = round(1.0 / (1.0 + item["rrf_score"] * 100), 4)
    finally:
        db.close()
    return fused


# ══════════════════════════════════════════════════════════════════
# 语义去重
# ══════════════════════════════════════════════════════════════════

DEDUP_THRESHOLD = 0.125  # 余弦距离阈值，低于此值视为重复（0=完全相同，1=无关）


def search_similar(title: str, description: str = "") -> tuple[int | None, float]:
    """
    在已有问题库中搜索与新问题最相似的记录。

    参数:
        title: 新问题的标题
        description: 新问题的描述（可选）

    返回:
        (problem_id, distance) — 最近匹配及其余弦距离。无匹配时返回 (None, 1.0)。
    """
    query_text = title
    if description:
        query_text = f"{title} {description}"

    query_vector = _embed(query_text)
    collection = _get_collection()

    results = collection.query(
        query_embeddings=[query_vector],
        n_results=1,
        include=["metadatas", "distances"],
    )

    if not results["ids"] or not results["ids"][0]:
        return (None, 1.0)

    pid = int(results["ids"][0][0])
    distance = results["distances"][0][0] if results["distances"] else 1.0
    return (pid, distance)


# ── 全量重建 ───────────────────────────────────────────────────

def rebuild_index() -> dict:
    """全量重建向量索引和 FTS5 全文索引。"""
    db = SessionLocal()
    try:
        problems = db.query(Problem).all()
        if not problems:
            return {"indexed": 0, "errors": 0}

        # 向量索引重建
        collection = _get_collection()
        try:
            existing_ids = collection.get()["ids"]
            if existing_ids:
                collection.delete(ids=existing_ids)
        except Exception:
            pass

        docs = []
        valid_problems = []
        for problem in problems:
            doc = _build_document(problem)
            if doc.strip():
                docs.append(doc)
                valid_problems.append(problem)

        if docs:
            vectors = _embed_batch(docs)
            ids = []
            metadatas = []
            for problem in valid_problems:
                ids.append(str(problem.id))
                metadatas.append({
                    "problem_id": problem.id,
                    "project_id": problem.project_id or 0,
                    "title": problem.title or "",
                    "tech_stack": problem.tech_stack or "",
                    "priority_score": problem.priority_score or 5,
                })
            collection.add(ids=ids, embeddings=vectors, documents=docs, metadatas=metadatas)

        # FTS5 全文索引重建
        _rebuild_fts()

        errors = len(problems) - len(ids)
        return {"indexed": len(ids), "errors": errors}
    finally:
        db.close()


def index_stats() -> dict:
    try:
        collection = _get_collection()
        v_count = collection.count()
        db = SessionLocal()
        try:
            from sqlalchemy import text
            fts_count = db.execute(text(
                "SELECT COUNT(*) FROM problems_fts"
            )).scalar()
        finally:
            db.close()
        return {
            "collection": COLLECTION_NAME,
            "vector_docs": v_count,
            "fts_docs": fts_count or 0,
        }
    except Exception:
        return {"collection": COLLECTION_NAME, "vector_docs": 0, "fts_docs": 0}
