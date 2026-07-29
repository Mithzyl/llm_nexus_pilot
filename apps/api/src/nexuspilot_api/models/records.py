"""Tool, artifact, evaluation, and outbox persistence models."""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import JSON, BigInteger, DateTime, ForeignKey, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from nexuspilot_api.models.base import Base, new_id


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
