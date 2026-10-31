"""Whole-turn response identities; request and event bodies remain in object storage."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from nexuspilot_api.models.base import Base, TimestampMixin, new_id


class QuickResponseExecution(TimestampMixin, Base):
    """Persist a single accepted quick response, separate from its model attempts."""

    __tablename__ = "quick_response_executions"
    __table_args__ = (UniqueConstraint("owner_user_id", "request_key", name="uq_quick_owner_key"),)
    response_execution_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("llm_runs.run_id"), unique=True)
    owner_user_id: Mapped[str] = mapped_column(ForeignKey("users.user_id"))
    request_key: Mapped[str] = mapped_column(String(128))
    request_uri: Mapped[str] = mapped_column(String(1024))
    status: Mapped[str] = mapped_column(String(32), default="queued")
    assistant_message_id: Mapped[str | None] = mapped_column(String(36))
    execution_owner: Mapped[str | None] = mapped_column(String(128))
    lease_version: Mapped[int] = mapped_column(Integer, default=0)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class QuickResponseEvent(Base):
    """Index immutable whole-turn event objects for finite replay and live observation."""

    __tablename__ = "quick_response_events"
    response_execution_id: Mapped[str] = mapped_column(
        ForeignKey("quick_response_executions.response_execution_id"), primary_key=True
    )
    sequence: Mapped[int] = mapped_column(Integer, primary_key=True)
    body_uri: Mapped[str] = mapped_column(String(1024))
