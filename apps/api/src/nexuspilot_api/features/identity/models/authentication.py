"""Authentication identities, password verifiers, browser sessions, and audit facts."""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from nexuspilot_api.models.base import Base, TimestampMixin, new_id, utc_now


class UserAuthenticationIdentity(TimestampMixin, Base):
    """Bind one normalized login identity to the existing platform User owner."""

    __tablename__ = "user_auth_identities"
    __table_args__ = (
        UniqueConstraint(
            "identity_provider",
            "normalized_email",
            name="uq_user_auth_identity_provider_email",
        ),
        Index("ix_user_auth_identity_user_provider", "user_id", "identity_provider"),
    )

    auth_identity_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.user_id", ondelete="CASCADE"),
        index=True,
    )
    identity_provider: Mapped[str] = mapped_column(String(32), default="local_password")
    email: Mapped[str] = mapped_column(String(320))
    normalized_email: Mapped[str] = mapped_column(String(320))
    is_verified: Mapped[bool] = mapped_column(default=True)


class UserPasswordCredential(Base):
    """Store an Argon2id password verifier without retaining the original password."""

    __tablename__ = "user_password_credentials"

    auth_identity_id: Mapped[str] = mapped_column(
        ForeignKey("user_auth_identities.auth_identity_id", ondelete="CASCADE"),
        primary_key=True,
    )
    password_hash: Mapped[str] = mapped_column(String(512))
    password_hash_algorithm: Mapped[str] = mapped_column(String(32), default="argon2id")
    password_hash_version: Mapped[int] = mapped_column(Integer, default=1)
    password_changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class UserLoginSession(TimestampMixin, Base):
    """Represent one revocable browser login while storing only token digests."""

    __tablename__ = "user_login_sessions"
    __table_args__ = (
        UniqueConstraint("session_token_hash", name="uq_user_login_session_token_hash"),
        Index(
            "ix_user_login_session_user_revoked_expires",
            "user_id",
            "revoked_at",
            "absolute_expires_at",
        ),
    )

    login_session_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.user_id", ondelete="CASCADE"),
        index=True,
    )
    auth_identity_id: Mapped[str] = mapped_column(
        ForeignKey("user_auth_identities.auth_identity_id", ondelete="CASCADE"),
        index=True,
    )
    session_token_hash: Mapped[str] = mapped_column(String(64))
    csrf_token_hash: Mapped[str] = mapped_column(String(64))
    device_name: Mapped[str | None] = mapped_column(String(128))
    authenticated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    last_used_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    idle_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    absolute_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_reason: Mapped[str | None] = mapped_column(String(64))


class AuthenticationAuditEvent(Base):
    """Record authentication outcomes without passwords, tokens, or raw email addresses."""

    __tablename__ = "authentication_audit_events"
    __table_args__ = (
        Index(
            "ix_auth_audit_rate_limit",
            "event_type",
            "identifier_fingerprint",
            "client_fingerprint",
            "occurred_at",
        ),
        Index("ix_auth_audit_user_occurred", "user_id", "occurred_at"),
    )

    authentication_audit_event_id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=new_id,
    )
    user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.user_id", ondelete="SET NULL"),
        index=True,
    )
    auth_identity_id: Mapped[str | None] = mapped_column(
        ForeignKey("user_auth_identities.auth_identity_id", ondelete="SET NULL"),
        index=True,
    )
    login_session_id: Mapped[str | None] = mapped_column(
        ForeignKey("user_login_sessions.login_session_id", ondelete="SET NULL"),
        index=True,
    )
    event_type: Mapped[str] = mapped_column(String(64))
    outcome: Mapped[str] = mapped_column(String(32))
    identifier_fingerprint: Mapped[str | None] = mapped_column(String(64))
    client_fingerprint: Mapped[str | None] = mapped_column(String(64))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
