"""Pending email verification, separate from active users and login credentials."""

from datetime import datetime

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from nexuspilot_api.models.base import Base


class RegistrationChallenge(Base):
    """Keep one encrypted short-lived verification code per email, never a password."""

    __tablename__ = "registration_challenges"
    normalized_email: Mapped[str] = mapped_column(String(320), primary_key=True)
    encrypted_code: Mapped[str | None] = mapped_column(String(512))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    delivered: Mapped[bool] = mapped_column(default=False)
    failed_attempts: Mapped[int] = mapped_column(Integer, default=0)
