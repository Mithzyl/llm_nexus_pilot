"""Private immutable uploads with durable reservation and cleanup state."""

from datetime import datetime

from sqlalchemy import JSON, BigInteger, DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from nexuspilot_api.models.base import Base, TimestampMixin, new_id


class UserFile(TimestampMixin, Base):
    """Keep one immutable upload and parsed result; failed/deleting rows retain cleanup keys."""

    __tablename__ = "user_files"
    __table_args__ = (
        UniqueConstraint("user_id", "key_hash", name="uq_user_file_request"),
        Index("ix_user_file_owner_status", "user_id", "status"),
    )
    file_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.user_id", ondelete="RESTRICT"))
    key_hash: Mapped[str] = mapped_column(String(64))
    filename: Mapped[str] = mapped_column(String(255))
    mime_type: Mapped[str] = mapped_column(String(255))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    content_hash: Mapped[str] = mapped_column(String(64))
    storage_uri: Mapped[str] = mapped_column(String(512))
    parsed_uri: Mapped[str] = mapped_column(String(512))
    parsed_hash: Mapped[str | None] = mapped_column(String(64))
    parsed_size: Mapped[int | None] = mapped_column(BigInteger)
    parse_summary: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(32), default="reserved")
    error: Mapped[str | None] = mapped_column(String(255))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    run_id: Mapped[str | None] = mapped_column(ForeignKey("llm_runs.run_id", ondelete="RESTRICT"))
