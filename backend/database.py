# -*- coding: utf-8 -*-
"""
DevQuest Log — 数据库初始化与连接管理

使用 SQLAlchemy 连接 SQLite，提供:
- 引擎创建（engine）
- 会话工厂（SessionLocal）
- 表结构自动创建（init_db）
"""

import os
from pathlib import Path

from sqlalchemy import create_engine
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


# ── 初始化函数 ────────────────────────────────────────────────
def init_db():
    """
    根据 models.py 中定义的所有 ORM 模型，自动创建数据库表。
    仅在表不存在时创建（不会覆盖已有数据）。
    """
    # 先导入 models，确保所有模型类被注册到 Base.metadata
    from backend import models  # noqa: F401
    Base.metadata.create_all(bind=engine)


# ── 依赖注入工具（供 FastAPI 路由使用）────────────────────────
def get_db():
    """
    生成器函数，为每个请求提供独立的数据库会话，
    并在请求结束后自动关闭会话。
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
