"""Two-party account associations; historical resources keep their original owners."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from nexuspilot_api.models.base import Base, new_id


class AccountLinkRequest(Base):
    """Record an App-authenticated request awaiting explicit Nexus browser consent."""

    __tablename__ = "account_link_requests"
    request_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    mapping_id: Mapped[str] = mapped_column(ForeignKey("application_user_mappings.mapping_id"))
    ticket_digest: Mapped[str] = mapped_column(String(64), unique=True)
    app_verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EcosystemAccountLink(Base):
    """Keep the approved primary/App pair and revocation version, not copied business data."""

    __tablename__ = "ecosystem_account_links"
    link_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    primary_principal_id: Mapped[str] = mapped_column(ForeignKey("users.user_id"), index=True)
    linked_principal_id: Mapped[str] = mapped_column(ForeignKey("users.user_id"), index=True)
    mapping_id: Mapped[str] = mapped_column(
        ForeignKey("application_user_mappings.mapping_id"), unique=True
    )
    status: Mapped[str] = mapped_column(String(16), default="active")
    app_verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    browser_verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(default=1)
