"""Single-use login grants; opaque credentials use the existing authentication digest scheme."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from nexuspilot_api.models.base import Base, new_id


class ApplicationLoginGrant(Base):
    """Bind a short login code to the approving browser session, client, callback and PKCE."""

    __tablename__ = "application_login_grants"
    grant_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    application_id: Mapped[str] = mapped_column(ForeignKey("applications.application_id"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.user_id"))
    login_session_id: Mapped[str] = mapped_column(
        ForeignKey("user_login_sessions.login_session_id")
    )
    code_digest: Mapped[str] = mapped_column(String(64), unique=True)
    redirect_uri: Mapped[str] = mapped_column(String(2048))
    code_challenge: Mapped[str] = mapped_column(String(43))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    registration_ticket_digest: Mapped[str | None] = mapped_column(String(64), unique=True)
    registration_completed: Mapped[bool] = mapped_column(Boolean, default=False)
