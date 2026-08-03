"""L1 Session State and Session Summary persistence models."""

from datetime import datetime

from sqlalchemy import (
    JSON,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from nexuspilot_api.models.base import Base, new_id, utc_now
from nexuspilot_api.models.enums import SessionMemoryStatus


class LlmSessionState(Base):
    """Represent one immutable version of a session's confirmed working state."""

    __tablename__ = "llm_session_states"
    __table_args__ = (
        UniqueConstraint(
            "session_id",
            "version",
            name="uq_session_state_version",
        ),
        UniqueConstraint(
            "session_id",
            "idempotency_key",
            name="uq_session_state_key",
        ),
        Index(
            "ix_session_state_session_status_version",
            "session_id",
            "status",
            "version",
        ),
    )

    session_state_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("llm_sessions.session_id", ondelete="CASCADE"),
        index=True,
    )
    schema_version: Mapped[str] = mapped_column(String(64), default="session_state.v1")
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[SessionMemoryStatus] = mapped_column(
        Enum(SessionMemoryStatus, native_enum=False, length=32),
        default=SessionMemoryStatus.GENERATING,
    )
    state_json: Mapped[dict] = mapped_column(JSON)
    previous_state_id: Mapped[str | None] = mapped_column(String(36))
    state_through_message_id: Mapped[str | None] = mapped_column(String(36))
    state_through_message_sequence: Mapped[int | None] = mapped_column(Integer)
    generation_attempt_id: Mapped[str | None] = mapped_column(
        ForeignKey("llm_attempts.attempt_id", ondelete="SET NULL"),
        index=True,
    )
    json_snapshot_object_id: Mapped[str | None] = mapped_column(String(36), index=True)
    markdown_snapshot_object_id: Mapped[str | None] = mapped_column(String(36), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(128))
    request_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        server_default=func.now(),
    )
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(128))


class LlmSessionSummary(Base):
    """Represent one immutable version of a session's bounded summary."""

    __tablename__ = "llm_session_summaries"
    __table_args__ = (
        UniqueConstraint(
            "session_id",
            "version",
            name="uq_session_summary_version",
        ),
        UniqueConstraint(
            "session_id",
            "idempotency_key",
            name="uq_session_summary_key",
        ),
        Index(
            "ix_session_summary_session_status_version",
            "session_id",
            "status",
            "version",
        ),
    )

    session_summary_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("llm_sessions.session_id", ondelete="CASCADE"),
        index=True,
    )
    schema_version: Mapped[str] = mapped_column(String(64), default="session_summary.v1")
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[SessionMemoryStatus] = mapped_column(
        Enum(SessionMemoryStatus, native_enum=False, length=32),
        default=SessionMemoryStatus.GENERATING,
    )
    summary_json: Mapped[dict] = mapped_column(JSON)
    previous_summary_id: Mapped[str | None] = mapped_column(String(36))
    summary_from_message_sequence: Mapped[int | None] = mapped_column(Integer)
    summary_through_message_id: Mapped[str | None] = mapped_column(String(36))
    summary_through_message_sequence: Mapped[int | None] = mapped_column(Integer)
    generation_attempt_id: Mapped[str | None] = mapped_column(
        ForeignKey("llm_attempts.attempt_id", ondelete="SET NULL"),
        index=True,
    )
    json_snapshot_object_id: Mapped[str | None] = mapped_column(String(36), index=True)
    markdown_snapshot_object_id: Mapped[str | None] = mapped_column(String(36), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(128))
    request_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        server_default=func.now(),
    )
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(128))
