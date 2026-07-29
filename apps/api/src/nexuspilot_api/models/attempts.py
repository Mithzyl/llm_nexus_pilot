"""Logical model-attempt and physical retry persistence models."""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
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
from sqlalchemy.orm import Mapped, mapped_column

from nexuspilot_api.models.base import Base, new_id
from nexuspilot_api.models.enums import AttemptStatus


class LlmAttempt(Base):
    """Record one logical model request including cost, latency, and failure data."""

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
