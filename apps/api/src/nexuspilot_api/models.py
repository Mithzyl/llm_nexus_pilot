"""Persistent business facts for phase-one runs, tasks, calls, and artifacts."""

import enum
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def new_id() -> str:
    """Generate a sortable-independent UUID string for externally visible records."""

    return str(uuid.uuid4())


class Base(DeclarativeBase):
    """Base class shared by all SQLAlchemy table mappings."""


class TimestampMixin:
    """Add database-managed creation and update timestamps to mutable records."""

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class RunStatus(str, enum.Enum):
    """Lifecycle states for an end-to-end user request."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskStatus(str, enum.Enum):
    """Lifecycle states reserved for synchronous and future asynchronous execution."""

    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    WAITING_FOR_INPUT = "waiting_for_input"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    WAITING_FOR_DEPENDENCY = "waiting_for_dependency"
    RETRY_SCHEDULED = "retry_scheduled"
    CANCEL_REQUESTED = "cancel_requested"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AttemptStatus(str, enum.Enum):
    """Completion states for one physical provider request."""

    STARTED = "started"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"


class User(TimestampMixin, Base):
    """Represent a platform user referenced by authenticated run records."""

    __tablename__ = "users"

    user_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(default=True)


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


class LlmAttempt(Base):
    """Record one actual model provider request including cost, latency, and failure data."""

    __tablename__ = "llm_attempts"
    __table_args__ = (Index("ix_attempt_run_task", "run_id", "task_id"),)

    attempt_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("llm_runs.run_id", ondelete="CASCADE"))
    task_id: Mapped[str | None] = mapped_column(
        ForeignKey("llm_tasks.task_id", ondelete="SET NULL")
    )
    provider: Mapped[str] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(String(128))
    request_type: Mapped[str] = mapped_column(String(64), default="generation")
    request_key: Mapped[str | None] = mapped_column(String(128), unique=True)
    retry_count: Mapped[int] = mapped_column(default=0)
    status: Mapped[AttemptStatus] = mapped_column(Enum(AttemptStatus, native_enum=False, length=32))
    input_tokens: Mapped[int | None]
    output_tokens: Mapped[int | None]
    cached_tokens: Mapped[int | None]
    estimated_cost: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    latency_ms: Mapped[int | None]
    provider_request_id: Mapped[str | None] = mapped_column(String(255))
    raw_request_uri: Mapped[str | None] = mapped_column(String(1024))
    raw_response_uri: Mapped[str | None] = mapped_column(String(1024))
    error_code: Mapped[str | None] = mapped_column(String(128))
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LlmAttemptRetry(Base):
    """Record one physical HTTP request belonging to a logical model attempt."""

    __tablename__ = "llm_attempt_retries"
    __table_args__ = (UniqueConstraint("attempt_id", "attempt_index"),)

    retry_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    attempt_id: Mapped[str] = mapped_column(
        ForeignKey("llm_attempts.attempt_id", ondelete="CASCADE"), index=True
    )
    attempt_index: Mapped[int]
    status_code: Mapped[int | None]
    latency_ms: Mapped[int]
    error_type: Mapped[str | None] = mapped_column(String(128))
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LlmToolCall(Base):
    """Record one permission-controlled tool invocation made by a model attempt."""

    __tablename__ = "llm_tool_calls"

    tool_call_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    attempt_id: Mapped[str] = mapped_column(
        ForeignKey("llm_attempts.attempt_id", ondelete="CASCADE"), index=True
    )
    tool_name: Mapped[str] = mapped_column(String(128))
    risk_level: Mapped[str] = mapped_column(String(32))
    input_json: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(32))
    permission_decision: Mapped[str | None] = mapped_column(String(32))
    result_uri: Mapped[str | None] = mapped_column(String(1024))
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LlmArtifact(Base):
    """Store searchable metadata for content kept in MinIO rather than MySQL."""

    __tablename__ = "llm_artifacts"

    artifact_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("llm_runs.run_id", ondelete="CASCADE"), index=True
    )
    task_id: Mapped[str | None] = mapped_column(
        ForeignKey("llm_tasks.task_id", ondelete="SET NULL"), index=True
    )
    artifact_type: Mapped[str] = mapped_column(String(64))
    filename: Mapped[str] = mapped_column(String(255))
    mime_type: Mapped[str] = mapped_column(String(255))
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    # Keep the unique key below MySQL/InnoDB's utf8mb4 index-size limit.
    storage_uri: Mapped[str] = mapped_column(String(512), unique=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LlmEvaluation(Base):
    """Record an independent or deterministic evaluation of a task result."""

    __tablename__ = "llm_evaluations"

    evaluation_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("llm_runs.run_id", ondelete="CASCADE"), index=True
    )
    task_id: Mapped[str] = mapped_column(ForeignKey("llm_tasks.task_id", ondelete="CASCADE"))
    candidate_attempt_id: Mapped[str | None] = mapped_column(
        ForeignKey("llm_attempts.attempt_id", ondelete="SET NULL")
    )
    evaluator_attempt_id: Mapped[str | None] = mapped_column(
        ForeignKey("llm_attempts.attempt_id", ondelete="SET NULL")
    )
    evaluation_type: Mapped[str] = mapped_column(String(64))
    verdict: Mapped[str] = mapped_column(String(32))
    score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    findings_json: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LlmOutboxEvent(Base):
    """Persist future RabbitMQ messages transactionally until a publisher confirms delivery."""

    __tablename__ = "llm_outbox_events"

    event_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    aggregate_type: Mapped[str] = mapped_column(String(64))
    aggregate_id: Mapped[str] = mapped_column(String(36), index=True)
    event_type: Mapped[str] = mapped_column(String(128), index=True)
    payload_json: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    publish_attempts: Mapped[int] = mapped_column(default=0)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
