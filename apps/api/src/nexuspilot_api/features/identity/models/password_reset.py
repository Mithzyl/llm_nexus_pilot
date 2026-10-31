"""Purpose-specific reset challenges, never interchangeable with registration proof."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from nexuspilot_api.models.base import Base


class PasswordResetChallenge(Base):
    """Pin an encrypted short-lived code to an existing credential version."""

    __tablename__ = "password_reset_challenges"
    normalized_email: Mapped[str] = mapped_column(String(320), primary_key=True)
    auth_identity_id: Mapped[str | None] = mapped_column(
        ForeignKey("user_auth_identities.auth_identity_id")
    )
    encrypted_code: Mapped[str | None] = mapped_column(String(512))
    credential_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    delivered: Mapped[bool] = mapped_column(default=False)
    failed_attempts: Mapped[int] = mapped_column(default=0)
