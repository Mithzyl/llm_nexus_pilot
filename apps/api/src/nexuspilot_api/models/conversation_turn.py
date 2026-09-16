"""Persist keyed turn identity without changing existing Run or Message columns."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from nexuspilot_api.models.base import Base, utc_now


class LlmConversationTurn(Base):
    """Bind a session-scoped request key to exactly one committed Run and User Message."""

    __tablename__ = "llm_conversation_turns"
    __table_args__ = (
        UniqueConstraint("run_id", name="uq_llm_conversation_turn_run"),
        UniqueConstraint("message_id", name="uq_llm_conversation_turn_message"),
    )

    session_id: Mapped[str] = mapped_column(
        ForeignKey("llm_sessions.session_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    key_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    run_id: Mapped[str] = mapped_column(ForeignKey("llm_runs.run_id", ondelete="RESTRICT"))
    message_id: Mapped[str] = mapped_column(
        ForeignKey("llm_messages.message_id", ondelete="RESTRICT"),
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
