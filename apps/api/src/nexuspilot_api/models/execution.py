"""Run, task, and task-dependency persistence models."""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, Enum, ForeignKey, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from nexuspilot_api.models.base import Base, TimestampMixin, new_id
from nexuspilot_api.models.enums import RunStatus, TaskStatus


class LlmRun(TimestampMixin, Base):
    """Represent one complete user request and its budget consumption."""

    __tablename__ = "llm_runs"

    run_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.user_id"), index=True)
    session_id: Mapped[str | None] = mapped_column(String(128), index=True)
    user_request: Mapped[str] = mapped_column(Text)
    run_type: Mapped[str] = mapped_column(String(64), default="general")
    status: Mapped[RunStatus] = mapped_column(
        Enum(RunStatus, native_enum=False, length=32), default=RunStatus.PENDING, index=True
    )
    budget_limit: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    cost_used: Mapped[Decimal] = mapped_column(Numeric(12, 6), default=Decimal("0"))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    tasks: Mapped[list["LlmTask"]] = relationship(back_populates="run")


class LlmTask(TimestampMixin, Base):
    """Represent one concrete work item belonging to a run."""

    __tablename__ = "llm_tasks"

    task_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("llm_runs.run_id", ondelete="CASCADE"), index=True
    )
    parent_task_id: Mapped[str | None] = mapped_column(
        ForeignKey("llm_tasks.task_id", ondelete="SET NULL"), index=True
    )
    task_type: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(255))
    objective: Mapped[str] = mapped_column(Text)
    assigned_role: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[TaskStatus] = mapped_column(
        Enum(TaskStatus, native_enum=False, length=32), default=TaskStatus.PENDING, index=True
    )
    priority: Mapped[int] = mapped_column(default=0)
    max_attempts: Mapped[int] = mapped_column(default=3)
    current_attempt: Mapped[int] = mapped_column(default=0)
    timeout_seconds: Mapped[int] = mapped_column(default=300)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    run: Mapped[LlmRun] = relationship(back_populates="tasks")


class LlmTaskDependency(Base):
    """Store a directed prerequisite edge between two tasks in the same run."""

    __tablename__ = "llm_task_dependencies"
    __table_args__ = (UniqueConstraint("task_id", "depends_on_task_id"),)

    task_id: Mapped[str] = mapped_column(
        ForeignKey("llm_tasks.task_id", ondelete="CASCADE"), primary_key=True
    )
    depends_on_task_id: Mapped[str] = mapped_column(
        ForeignKey("llm_tasks.task_id", ondelete="CASCADE"), primary_key=True
    )
    dependency_type: Mapped[str] = mapped_column(String(32), default="completion")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
