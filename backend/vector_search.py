# -*- coding: utf-8 -*-
"""
DevQuest Log — ChromaDB 向量搜索模块

封装 ChromaDB 的索引构建与语义搜索逻辑：
- 新增问题时自动同步向量索引
- 支持语义搜索 + 技术栈过滤
- /rebuild-index 全量重建索引

Embedding 使用阿里百炼 text-embedding-v3（OpenAI 兼容格式）。
存储内容：问题标题 + 描述 + 解决方案的拼接文本
元数据：id / project_id / title / tech_stack / priority_score
"""

import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
load_dotenv()

import chromadb
from openai import OpenAI

from backend.database import SessionLocal
from backend.models import Problem

# ── 阿里百炼 Embedding 配置 ────────────────────────────────────
EMBEDDING_API_KEY = os.getenv("EMBEDDING_API_KEY", "")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-v3")
EMBEDDING_BASE_URL = os.getenv(
    "EMBEDDING_BASE_URL",
    "https://dashscope.aliyuncs.com/compatible-mode/v1",
)

# ── ChromaDB 持久化路径 ────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent
CHROMA_DIR = BASE_DIR / "data" / "chroma_db"
CHROMA_DIR.mkdir(parents=True, exist_ok=True)

COLLECTION_NAME = "problems"

# ── 全局客户端（延迟初始化）─────────────────────────────────────
_client: Optional[chromadb.PersistentClient] = None
_embed_client: Optional[OpenAI] = None
_collection: Optional[chromadb.Collection] = None


def _get_embed_client() -> OpenAI:
    """获取 OpenAI 兼容的 Embedding 客户端。"""
    global _embed_client
    if _embed_client is None:
        _embed_client = OpenAI(
            api_key=EMBEDDING_API_KEY,
            base_url=EMBEDDING_BASE_URL,
        )
    return _embed_client


def _get_collection() -> chromadb.Collection:
    """获取或创建 ChromaDB collection。"""
    global _client, _collection
    if _collection is None:
        _client = chromadb.PersistentClient(path=str(CHROMA_DIR))
        _collection = _client.get_or_create_collection(
            name=COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )
    return _collection


def _embed(text: str) -> list[float]:
    """将单段文本向量化。"""
    client = _get_embed_client()
    response = client.embeddings.create(model=EMBEDDING_MODEL, input=text)
    return response.data[0].embedding


def _embed_batch(texts: list[str]) -> list[list[float]]:
    """批量向量化。"""
    client = _get_embed_client()
    response = client.embeddings.create(model=EMBEDDING_MODEL, input=texts)
    # 按 input 顺序返回
    return [d.embedding for d in response.data]


# ── 文档拼接 ───────────────────────────────────────────────────

def _build_document(problem: Problem) -> str:
    """将问题拼接为待向量化的文本。"""
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
    将单个问题加入向量索引。

    参数:
        problem_id: 问题数据库 ID

    返回:
        bool: 是否成功
    """
    db = SessionLocal()
    try:
        problem = db.query(Problem).filter_by(id=problem_id).first()
        if not problem:
            return False

        doc = _build_document(problem)
        if not doc.strip():
            return False

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
        return True
    except Exception:
        return False
    finally:
        db.close()


def add_to_index_batch(problem_ids: list[int]) -> int:
    """批量加入向量索引，返回成功数量。"""
    count = 0
    for pid in problem_ids:
        if add_to_index(pid):
            count += 1
    return count


def delete_from_index(problem_id: int) -> bool:
    """从向量索引中删除指定问题。"""
    try:
        collection = _get_collection()
        collection.delete(ids=[str(problem_id)])
        return True
    except Exception:
        return False


# ── 语义搜索 ───────────────────────────────────────────────────

def search(
    query_text: str,
    k: int = 5,
    tech_filter: Optional[str] = None,
) -> list[dict]:
    """
    语义搜索相似问题。

    参数:
        query_text: 查询文本（自然语言描述）
        k: 返回结果数量，默认 5
        tech_filter: 可选，按技术栈关键词过滤

    返回:
        list[dict]: 搜索结果列表，每项包含:
            - problem_id / title / tech_stack / priority_score
            - document: 问题完整文本
            - distance: 向量距离（越小越相似）
    """
    collection = _get_collection()
    query_vector = _embed(query_text)

    # 有过滤条件时多取一些，避免过滤后不够 k 条
    fetch_k = k * 3 if tech_filter else k

    results = collection.query(
        query_embeddings=[query_vector],
        n_results=fetch_k,
        include=["documents", "metadatas", "distances"],
    )

    output = []
    if not results["ids"] or not results["ids"][0]:
        return output

    ids = results["ids"][0]
    docs = results["documents"][0] if results["documents"] else []
    metas = results["metadatas"][0] if results["metadatas"] else []
    distances = results["distances"][0] if results["distances"] else []

    for i, pid in enumerate(ids):
        item_tech = metas[i].get("tech_stack", "") if i < len(metas) else ""
        # Python 侧按技术栈过滤
        if tech_filter and tech_filter.lower() not in item_tech.lower():
            continue
        item = {
            "problem_id": int(pid),
            "title": metas[i].get("title", "") if i < len(metas) else "",
            "tech_stack": item_tech,
            "priority_score": metas[i].get("priority_score", 0) if i < len(metas) else 0,
            "document": docs[i] if i < len(docs) else "",
            "distance": distances[i] if i < len(distances) else 0.0,
        }
        output.append(item)

    output.sort(key=lambda x: x["distance"])
    return output[:k]


# ── 全量重建 ───────────────────────────────────────────────────

def rebuild_index() -> dict:
    """
    从 SQLite 全量重建 ChromaDB 向量索引。

    返回:
        dict: {"indexed": N, "errors": M}
    """
    db = SessionLocal()
    try:
        problems = db.query(Problem).all()
        if not problems:
            return {"indexed": 0, "errors": 0}

        collection = _get_collection()

        # 清空现有索引
        try:
            existing_ids = collection.get()["ids"]
            if existing_ids:
                collection.delete(ids=existing_ids)
        except Exception:
            pass

        # 构建文档并批量向量化
        docs = []
        valid_problems = []
        for problem in problems:
            doc = _build_document(problem)
            if doc.strip():
                docs.append(doc)
                valid_problems.append(problem)

        if not docs:
            return {"indexed": 0, "errors": len(problems)}

        # 批量 Embedding
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

        collection.add(
            ids=ids,
            embeddings=vectors,
            documents=docs,
            metadatas=metadatas,
        )

        errors = len(problems) - len(ids)
        return {"indexed": len(ids), "errors": errors}
    finally:
        db.close()


# ── 统计 ────────────────────────────────────────────────────────

def index_stats() -> dict:
    """返回当前向量索引的统计信息。"""
    try:
        collection = _get_collection()
        count = collection.count()
        return {"collection": COLLECTION_NAME, "documents": count}
    except Exception:
        return {"collection": COLLECTION_NAME, "documents": 0}
