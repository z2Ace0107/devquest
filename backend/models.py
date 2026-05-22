# -*- coding: utf-8 -*-
"""
DevQuest — ORM 数据模型

核心表:
- Project（项目）: 存储项目名称与创建时间
- Problem（问题）: 结构化技术问题记录
- Topic（主题）: 聚合相关问题为知识主题
- Concept（概念）: 技术/工具/错误等概念节点
- Link（链接）: 实体间双向关系
- AgentAction（操作日志）: Agent 操作审计
"""

from datetime import datetime, timezone

from sqlalchemy import (
    Column, Integer, String, Text, Float, TIMESTAMP,
    ForeignKey, Index,
)
from sqlalchemy.orm import relationship

from backend.database import Base


class Project(Base):
    """
    项目表 — 每个被追踪的编码项目对应一条记录。
    """
    __tablename__ = "projects"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), unique=True, nullable=False, comment="项目名称")
    created_at = Column(
        TIMESTAMP,
        default=lambda: datetime.now(timezone.utc).replace(tzinfo=None),
        nullable=False,
        comment="创建时间"
    )

    # 反向关联：可通过 project.problems 直接访问该项目下所有问题
    problems = relationship(
        "Problem",
        back_populates="project",
        cascade="all, delete-orphan",
    )

    def __repr__(self):
        return f"<Project id={self.id} name='{self.name}'>"


class Problem(Base):
    """
    问题表 — 从 AI 对话中提取的结构化技术问题记录。
    """
    __tablename__ = "problems"

    id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(
        Integer,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="所属项目 ID"
    )
    title = Column(Text, nullable=True, comment="问题标题（一句话总结）")
    description = Column(Text, nullable=True, comment="问题详细描述")
    attempts = Column(Text, nullable=True, comment="尝试过的方案（JSON 列表字符串）")
    solution = Column(Text, nullable=True, comment="最终解决方案（详细步骤）")
    tech_stack = Column(Text, nullable=True, comment="涉及技术栈（逗号分隔）")
    problem_type = Column(Text, nullable=True, comment="问题类型: Bug/性能优化/架构决策/环境配置/API调试")
    priority_score = Column(Integer, default=5, nullable=False, comment="优先级评分 1-10")
    raw_conversation = Column(Text, nullable=True, comment="原始对话片段")
    star_story = Column(Text, nullable=True, comment="STAR 故事（JSON 结构）")
    usage_count = Column(Integer, default=0, nullable=False, comment="被检索并使用次数（隐式反馈信号）")
    created_at = Column(
        TIMESTAMP,
        default=lambda: datetime.now(timezone.utc).replace(tzinfo=None),
        nullable=False,
        comment="记录创建时间"
    )
    first_seen_at = Column(
        TIMESTAMP,
        nullable=True,
        comment="首次入库时间（用于时效衰减计算，空则回退到 created_at）"
    )
    environment = Column(Text, nullable=True, comment="运行环境 JSON: {os, python, docker, ...}")
    feedback_score = Column(Float, default=0.0, nullable=False, comment="有用率 0-1")
    feedback_count = Column(Integer, default=0, nullable=False, comment="反馈总次数")
    solution_version = Column(Integer, default=1, nullable=False, comment="解法迭代版本")

    # 正向关联：通过 problem.project 访问所属项目
    project = relationship("Project", back_populates="problems")

    def __repr__(self):
        return f"<Problem id={self.id} title='{self.title}'>"

    def to_dict(self):
        return {
            "id": self.id,
            "project_id": self.project_id,
            "project_name": self.project.name if self.project else None,
            "title": self.title,
            "description": self.description,
            "attempts": self.attempts,
            "solution": self.solution,
            "tech_stack": self.tech_stack,
            "problem_type": self.problem_type,
            "priority_score": self.priority_score,
            "raw_conversation": self.raw_conversation,
            "star_story": self.star_story,
            "usage_count": self.usage_count,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "first_seen_at": self.first_seen_at.isoformat() if self.first_seen_at else None,
            "environment": self.environment,
            "feedback_score": self.feedback_score,
            "feedback_count": self.feedback_count,
            "solution_version": self.solution_version,
        }


# ── V4.0 新增模型 ────────────────────────────────────────────


class Topic(Base):
    """知识主题 — 聚合同类 Problem 为可编译的知识单元。"""
    __tablename__ = "topics"

    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String(255), nullable=False, unique=True, comment="主题名称")
    summary = Column(Text, nullable=True, comment="LLM 维护的主题摘要")
    first_seen_at = Column(TIMESTAMP, nullable=True, comment="首次出现时间")
    updated_at = Column(
        TIMESTAMP,
        default=lambda: datetime.now(timezone.utc).replace(tzinfo=None),
        onupdate=lambda: datetime.now(timezone.utc).replace(tzinfo=None),
        nullable=False,
        comment="最后更新时间"
    )
    freshness_score = Column(Float, default=1.0, nullable=False, comment="新鲜度 0-1")
    feishu_doc_id = Column(String(255), nullable=True, comment="飞书文档 ID")
    feishu_base_record_id = Column(String(255), nullable=True, comment="飞书多维表格记录 ID")
    solution_status = Column(
        String(20), default="需跟进",
        comment="解决状态: 已解决 / 需跟进 / 已过时"
    )
    problem_count = Column(Integer, default=0, nullable=False, comment="关联 Problem 数")
    project_count = Column(Integer, default=0, nullable=False, comment="跨项目数")
    created_at = Column(
        TIMESTAMP,
        default=lambda: datetime.now(timezone.utc).replace(tzinfo=None),
        nullable=False,
        comment="创建时间"
    )

    def __repr__(self):
        return f"<Topic id={self.id} title='{self.title}'>"

    def to_dict(self):
        return {
            "id": self.id,
            "title": self.title,
            "summary": self.summary,
            "first_seen_at": self.first_seen_at.isoformat() if self.first_seen_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "freshness_score": self.freshness_score,
            "feishu_doc_id": self.feishu_doc_id,
            "feishu_base_record_id": self.feishu_base_record_id,
            "solution_status": self.solution_status,
            "problem_count": self.problem_count,
            "project_count": self.project_count,
        }


class Concept(Base):
    """概念节点 — 知识图谱中的实体，可被多个 Problem/Topic 引用。"""
    __tablename__ = "concepts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False, unique=True, comment="概念名称")
    type = Column(
        String(50), nullable=False, default="技术",
        comment="概念类型: 技术 / 工具 / 错误 / 项目 / 模式"
    )
    aliases = Column(Text, nullable=True, comment="别名 JSON 数组")
    created_at = Column(
        TIMESTAMP,
        default=lambda: datetime.now(timezone.utc).replace(tzinfo=None),
        nullable=False,
    )

    def __repr__(self):
        return f"<Concept id={self.id} name='{self.name}' type='{self.type}'>"


class Link(Base):
    """双向链接 — 连接知识图谱中任意两个实体。"""
    __tablename__ = "links"
    __table_args__ = (
        Index("idx_source", "source_type", "source_id"),
        Index("idx_target", "target_type", "target_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    source_type = Column(
        String(20), nullable=False,
        comment="源实体类型: Problem / Topic / Concept"
    )
    source_id = Column(Integer, nullable=False, comment="源实体 ID")
    target_type = Column(
        String(20), nullable=False,
        comment="目标实体类型: Problem / Topic / Concept"
    )
    target_id = Column(Integer, nullable=False, comment="目标实体 ID")
    relation_type = Column(
        String(20), nullable=False, default="关联",
        comment="关系类型: 同类 / 属于 / 依赖 / 替代 / 导致 / 关联"
    )
    created_at = Column(
        TIMESTAMP,
        default=lambda: datetime.now(timezone.utc).replace(tzinfo=None),
        nullable=False,
    )

    def __repr__(self):
        return (f"<Link {self.source_type}#{self.source_id}"
                f" -[{self.relation_type}]-> "
                f"{self.target_type}#{self.target_id}>")


class AgentAction(Base):
    """Agent 操作日志 — 审计每一次 Agent 决策与执行结果。"""
    __tablename__ = "agent_actions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    action_type = Column(String(50), nullable=False, comment="操作类型")
    target_ids = Column(Text, nullable=True, comment="目标 ID 列表 JSON")
    result = Column(Text, nullable=True, comment="执行结果 JSON")
    created_at = Column(
        TIMESTAMP,
        default=lambda: datetime.now(timezone.utc).replace(tzinfo=None),
        nullable=False,
    )

    def __repr__(self):
        return f"<AgentAction id={self.id} type='{self.action_type}'>"
