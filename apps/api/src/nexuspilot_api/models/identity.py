"""Platform identity and conversation persistence models."""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from nexuspilot_api.models.base import Base, TimestampMixin, new_id, utc_now
from nexuspilot_api.models.enums import MessageRole, SessionStatus


class User(TimestampMixin, Base):
    """Represent a platform user referenced by authenticated run records."""

    __tablename__ = "users"

    user_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(default=True)
    current_memory_profile_snapshot_id: Mapped[str | None] = mapped_column(
        ForeignKey(
            "llm_user_memory_profile_snapshots.user_memory_profile_snapshot_id",
            ondelete="SET NULL",
        ),
        index=True,
    )


class LlmSession(TimestampMixin, Base):
    """Represent one durable conversation owned by a platform user."""

    __tablename__ = "llm_sessions"
    __table_args__ = (
        Index(
            "ix_llm_sessions_user_status_created",
            "user_id",
            "status",
            "created_at",
            "session_id",
        ),
    )

    session_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.user_id", ondelete="CASCADE"),
        index=True,
    )
    project_id: Mapped[str | None] = mapped_column(
        ForeignKey("llm_projects.project_id", ondelete="RESTRICT"),
        index=True,
    )
    title: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[SessionStatus] = mapped_column(
        Enum(SessionStatus, native_enum=False, length=32),
        default=SessionStatus.ACTIVE,
        index=True,
    )
    current_state_id: Mapped[str | None] = mapped_column(
        ForeignKey("llm_session_states.session_state_id", ondelete="SET NULL"),
        index=True,
    )
    current_summary_id: Mapped[str | None] = mapped_column(
        ForeignKey("llm_session_summaries.session_summary_id", ondelete="SET NULL"),
        index=True,
    )
    next_message_sequence: Mapped[int] = mapped_column(Integer, default=1)


class LlmMessage(Base):
    """Represent one immutable ordered message inside a durable conversation."""

    __tablename__ = "llm_messages"
    __table_args__ = (
        UniqueConstraint(
            "session_id",
            "sequence",
            name="uq_llm_messages_session_sequence",
        ),
    )

    message_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("llm_sessions.session_id", ondelete="CASCADE"),
        index=True,
    )
    run_id: Mapped[str | None] = mapped_column(
        ForeignKey("llm_runs.run_id", ondelete="SET NULL"),
        index=True,
    )
    source_model_attempt_id: Mapped[str | None] = mapped_column(
        ForeignKey("llm_attempts.attempt_id", ondelete="SET NULL"),
        index=True,
    )
    parent_message_id: Mapped[str | None] = mapped_column(
        ForeignKey("llm_messages.message_id", ondelete="SET NULL"),
        index=True,
    )
    role: Mapped[MessageRole] = mapped_column(
        Enum(MessageRole, native_enum=False, length=32),
    )
    content_type: Mapped[str] = mapped_column(String(64), default="text")
    content_text: Mapped[str | None] = mapped_column(Text)
    content_uri: Mapped[str | None] = mapped_column(String(512))
    sequence: Mapped[int] = mapped_column(Integer)
    token_count: Mapped[int | None] = mapped_column(Integer)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
    )
