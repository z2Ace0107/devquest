# -*- coding: utf-8 -*-
"""
DevQuest — 数据库初始化与连接管理

使用 SQLAlchemy 连接 SQLite，提供:
- 引擎创建（engine）
- 会话工厂（SessionLocal）
- 表结构自动创建（init_db）
"""

import os
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, declarative_base

# ── 数据库文件路径 ────────────────────────────────────────────
# 默认存放在项目根目录的 data/ 文件夹下
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    f"sqlite:///{DATA_DIR / 'devquest.db'}"
)

# ── 引擎 ──────────────────────────────────────────────────────
# SQLite 需要 check_same_thread=False 以支持多线程访问
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
    echo=False,  # 生产环境关闭 SQL 日志；调试时可改为 True
)

# ── 会话工厂 ──────────────────────────────────────────────────
SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
)

# ── ORM 基类 ──────────────────────────────────────────────────
Base = declarative_base()


# ── Migration 辅助 ──────────────────────────────────────────────

def _migrate_v2_columns(conn):
    """V3.0 新增列：静默迁移，列已存在时跳过。"""
    v2_columns = [
        ("first_seen_at", "TIMESTAMP"),
        ("environment", "TEXT"),
        ("feedback_score", "FLOAT DEFAULT 0.0"),
        ("feedback_count", "INTEGER DEFAULT 0"),
        ("solution_version", "INTEGER DEFAULT 1"),
    ]
    for col_name, col_type in v2_columns:
        try:
            conn.execute(text(
                f"ALTER TABLE problems ADD COLUMN {col_name} {col_type}"
            ))
        except Exception:
            pass


# ── V4.0 Migration ──────────────────────────────────────────────

def _migrate_v4_tables(conn):
    """V4.0 新增表：Topic / Concept / Link / AgentAction。已存在则跳过。"""
    v4_ddl = [
        # Topic
        ("topics", """
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title VARCHAR(255) NOT NULL UNIQUE,
            summary TEXT,
            first_seen_at TIMESTAMP,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            freshness_score FLOAT NOT NULL DEFAULT 1.0,
            feishu_doc_id VARCHAR(255),
            feishu_base_record_id VARCHAR(255),
            solution_status VARCHAR(20) DEFAULT '需跟进',
            problem_count INTEGER NOT NULL DEFAULT 0,
            project_count INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        """),
        # Concept
        ("concepts", """
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name VARCHAR(255) NOT NULL UNIQUE,
            type VARCHAR(50) NOT NULL DEFAULT '技术',
            aliases TEXT,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        """),
        # Link
        ("links", """
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_type VARCHAR(20) NOT NULL,
            source_id INTEGER NOT NULL,
            target_type VARCHAR(20) NOT NULL,
            target_id INTEGER NOT NULL,
            relation_type VARCHAR(20) NOT NULL DEFAULT '关联',
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        """),
        # AgentAction
        ("agent_actions", """
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            action_type VARCHAR(50) NOT NULL,
            target_ids TEXT,
            result TEXT,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        """),
    ]
    for table_name, columns_ddl in v4_ddl:
        try:
            conn.execute(text(
                f"CREATE TABLE IF NOT EXISTS {table_name} ({columns_ddl})"
            ))
        except Exception:
            pass

    # Link indexes
    for idx_col in ("source_type", "target_type"):
        try:
            conn.execute(text(
                f"CREATE INDEX IF NOT EXISTS idx_{idx_col} "
                f"ON links ({idx_col}, {idx_col[:-5]}_id)"
            ))
        except Exception:
            pass

    # Topic FTS
    try:
        conn.execute(text(
            "CREATE VIRTUAL TABLE IF NOT EXISTS topics_fts "
            "USING fts5(title, summary)"
        ))
    except Exception:
        pass


# ── 初始化函数 ────────────────────────────────────────────────
def init_db():
    """
    根据 models.py 中定义的所有 ORM 模型，自动创建数据库表。
    仅在表不存在时创建（不会覆盖已有数据），同时创建 FTS5 全文索引。
    """
    from backend import models  # noqa: F401
    Base.metadata.create_all(bind=engine)

    with engine.connect() as conn:
        # FTS5 关键词通道
        conn.execute(text(
            "CREATE VIRTUAL TABLE IF NOT EXISTS problems_fts "
            "USING fts5(title, description, solution)"
        ))
        # 向前兼容列
        try:
            conn.execute(text(
                "ALTER TABLE problems ADD COLUMN usage_count INTEGER DEFAULT 0"
            ))
        except Exception:
            pass
        _migrate_v2_columns(conn)
        # V4.2 溯源列
        try:
            conn.execute(text(
                "ALTER TABLE problems ADD COLUMN source_session_id VARCHAR(255)"
            ))
        except Exception:
            pass
        try:
            conn.execute(text(
                "ALTER TABLE problems ADD COLUMN captured_at TIMESTAMP"
            ))
        except Exception:
            pass
        # V4.2 飞书归档标志
        try:
            conn.execute(text(
                "ALTER TABLE problems ADD COLUMN feishu_archived INTEGER DEFAULT 0"
            ))
        except Exception:
            pass
        # V4.0 新表
        _migrate_v4_tables(conn)
        conn.commit()
