# -*- coding: utf-8 -*-
"""
DevQuest — ORM 数据模型

定义两个核心表:
- Project（项目）: 存储项目名称与创建时间
- Problem（问题）: 存储每个技术问题的完整信息，关联到某个项目
"""

from datetime import datetime, timezone

from sqlalchemy import (
    Column, Integer, String, Text, Float, TIMESTAMP,
    ForeignKey,
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
        """
        将模型实例转为字典，方便 API 响应序列化。
        """
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
