"""Tool, artifact, evaluation, and outbox persistence models."""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import JSON, BigInteger, DateTime, ForeignKey, Index, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from nexuspilot_api.models.base import Base, new_id, utc_now


class LlmModelToolCall(Base):
    """Record one permission-controlled tool invocation made by a model attempt."""

    __tablename__ = "llm_tool_calls"
    __table_args__ = (
        Index(
            "ix_tool_attempt_status_started_id",
            "attempt_id",
            "status",
            "started_at",
            "tool_call_id",
        ),
    )

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
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        server_default=func.now(),
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LlmRunArtifact(Base):
    """Store searchable metadata for content kept in MinIO rather than MySQL."""

    __tablename__ = "llm_artifacts"
    __table_args__ = (
        Index(
            "ix_artifact_run_type_created_id",
            "run_id",
            "artifact_type",
            "created_at",
            "artifact_id",
        ),
        Index(
            "ix_artifact_task_type_created_id",
            "task_id",
            "artifact_type",
            "created_at",
            "artifact_id",
        ),
    )

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
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        server_default=func.now(),
    )


class LlmTaskEvaluation(Base):
    """Record an independent or deterministic evaluation of a task result."""

    __tablename__ = "llm_evaluations"
    __table_args__ = (
        Index(
            "ix_evaluation_run_type_created_id",
            "run_id",
            "evaluation_type",
            "created_at",
            "evaluation_id",
        ),
        Index(
            "ix_evaluation_task_verdict_created_id",
            "task_id",
            "verdict",
            "created_at",
            "evaluation_id",
        ),
    )

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
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        server_default=func.now(),
    )


class LlmOutboxEvent(Base):
    """Persist future RabbitMQ messages transactionally until a publisher confirms delivery."""

    __tablename__ = "llm_outbox_events"
    __table_args__ = (
        Index(
            "ix_outbox_status_created_id",
            "status",
            "created_at",
            "event_id",
        ),
        Index(
            "ix_outbox_aggregate_created_id",
            "aggregate_type",
            "aggregate_id",
            "created_at",
            "event_id",
        ),
    )

    event_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    aggregate_type: Mapped[str] = mapped_column(String(64))
    aggregate_id: Mapped[str] = mapped_column(String(36), index=True)
    event_type: Mapped[str] = mapped_column(String(128), index=True)
    payload_json: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    publish_attempts: Mapped[int] = mapped_column(default=0)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        server_default=func.now(),
    )
