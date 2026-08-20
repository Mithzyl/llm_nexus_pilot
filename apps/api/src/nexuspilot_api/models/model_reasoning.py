"""Durable public reasoning projections, replay events, and private continuation metadata."""

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Index, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from nexuspilot_api.models.base import Base, new_id, utc_now


class LlmModelResponseEvent(Base):
    """Persist one sanitized model-response event for ordered reconnect replay."""

    __tablename__ = "llm_model_response_events"
    __table_args__ = (
        UniqueConstraint("attempt_id", "event_sequence"),
        Index("ix_model_response_event_attempt_sequence", "attempt_id", "event_sequence"),
    )

    event_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    attempt_id: Mapped[str] = mapped_column(
        ForeignKey("llm_attempts.attempt_id", ondelete="CASCADE"),
    )
    event_sequence: Mapped[int]
    event_type: Mapped[str] = mapped_column(String(64))
    reasoning_block_id: Mapped[str | None] = mapped_column(String(128))
    public_payload_json: Mapped[dict] = mapped_column(JSON)
    schema_version: Mapped[str] = mapped_column(String(64), default="conversation-stream.v2")
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        server_default=func.now(),
    )


class LlmReasoningBlock(Base):
    """Store the authoritative public projection of one model reasoning block."""

    __tablename__ = "llm_reasoning_blocks"
    __table_args__ = (
        UniqueConstraint("attempt_id", "block_index"),
        Index("ix_reasoning_block_attempt_index", "attempt_id", "block_index"),
    )

    reasoning_block_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    attempt_id: Mapped[str] = mapped_column(
        ForeignKey("llm_attempts.attempt_id", ondelete="CASCADE"),
    )
    block_index: Mapped[int]
    presentation_kind: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32))
    visible_text: Mapped[str | None] = mapped_column(Text)
    reasoning_tokens: Mapped[int | None]
    display_policy: Mapped[str] = mapped_column(String(32))
    final_event_sequence: Mapped[int | None]
    snapshot_version: Mapped[int] = mapped_column(default=1)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    first_visible_token_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LlmProviderContinuationState(Base):
    """Reference encrypted provider-only continuation data scoped to one parent invocation."""

    __tablename__ = "llm_provider_continuation_states"
    __table_args__ = (
        UniqueConstraint("parent_attempt_id"),
        Index("ix_provider_continuation_run_expiry", "run_id", "expires_at"),
    )

    continuation_state_id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=new_id
    )
    parent_attempt_id: Mapped[str] = mapped_column(
        ForeignKey("llm_attempts.attempt_id", ondelete="CASCADE"),
    )
    run_id: Mapped[str] = mapped_column(
        ForeignKey("llm_runs.run_id", ondelete="CASCADE"),
    )
    task_id: Mapped[str | None] = mapped_column(String(36))
    provider: Mapped[str] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(String(128))
    state_kind: Mapped[str] = mapped_column(String(64))
    encrypted_payload_uri: Mapped[str] = mapped_column(String(1024))
    encryption_key_version: Mapped[str] = mapped_column(String(32))
    payload_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        server_default=func.now(),
    )
