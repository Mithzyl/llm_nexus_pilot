"""Human interaction metadata; questions and replies are encrypted object references."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from nexuspilot_api.models.base import Base, TimestampMixin, new_id


class UserInteraction(TimestampMixin, Base):
    """Bind one user decision to one saved tool call and immutable execution owner."""

    __tablename__ = "user_interactions"
    interaction_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("llm_runs.run_id"), index=True)
    owner_user_id: Mapped[str] = mapped_column(ForeignKey("users.user_id"))
    tool_call_id: Mapped[str] = mapped_column(
        ForeignKey("llm_tool_calls.tool_call_id"), unique=True
    )
    kind: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default="pending")
    version: Mapped[int] = mapped_column(Integer, default=1)
    request_uri: Mapped[str] = mapped_column(String(1024))
    response_uri: Mapped[str | None] = mapped_column(String(1024))
    reply_key: Mapped[str | None] = mapped_column(String(128))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
