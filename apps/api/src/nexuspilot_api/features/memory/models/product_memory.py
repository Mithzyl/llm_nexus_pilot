"""Product memory metadata, separate from legacy experimental hash/text records."""

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from nexuspilot_api.models.base import Base, TimestampMixin, new_id


class MemoryRunPermission(Base):
    """Persist tool eligibility from request authentication for later Worker execution."""

    __tablename__ = "memory_run_permissions"
    run_id: Mapped[str] = mapped_column(ForeignKey("llm_runs.run_id"), primary_key=True)
    allowed: Mapped[bool] = mapped_column(Boolean, default=False)


class UserMemoryScope(TimestampMixin, Base):
    """Serialize mutations for one owner/entry/project and retain its inherited setting."""

    __tablename__ = "user_memory_scopes"
    __table_args__ = (UniqueConstraint("owner_user_id", "scope_key", name="uq_memory_scope_owner"),)
    scope_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    owner_user_id: Mapped[str] = mapped_column(ForeignKey("users.user_id"))
    entry: Mapped[str] = mapped_column(String(64))
    scope_key: Mapped[str] = mapped_column(String(128))
    project_id: Mapped[str | None] = mapped_column(ForeignKey("llm_projects.project_id"))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    version: Mapped[int] = mapped_column(Integer, default=0)
    native_use_grant: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class UserMemoryEntry(TimestampMixin, Base):
    """Point to encrypted content without keeping personal prose in SQL."""

    __tablename__ = "user_memory_entries"
    entry_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    scope_id: Mapped[str] = mapped_column(ForeignKey("user_memory_scopes.scope_id"), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(16))
    sensitive: Mapped[bool] = mapped_column(Boolean, default=False)
    body_uri: Mapped[str | None] = mapped_column(String(1024))
    source_run_id: Mapped[str | None] = mapped_column(ForeignKey("llm_runs.run_id"))
    disclosure_run_id: Mapped[str | None] = mapped_column(ForeignKey("llm_runs.run_id"))
    memory_type: Mapped[str] = mapped_column(String(32), default="free_text")
    schema_version: Mapped[int] = mapped_column(Integer, default=1)
    origin_owner_user_id: Mapped[str | None] = mapped_column(String(128))
    origin_entry: Mapped[str | None] = mapped_column(String(64))
    origin_scope_key: Mapped[str | None] = mapped_column(String(128))


class UserMemoryOperation(TimestampMixin, Base):
    """Replay exact operations through encrypted request comparison, without content hashes."""

    __tablename__ = "user_memory_operations"
    operation_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    scope_id: Mapped[str] = mapped_column(ForeignKey("user_memory_scopes.scope_id"))
    request_key: Mapped[str] = mapped_column(String(128))
    request_uri: Mapped[str] = mapped_column(String(1024))
    result_json: Mapped[dict] = mapped_column(JSON)
    __table_args__ = (UniqueConstraint("scope_id", "request_key", name="uq_memory_operation_key"),)


class UserMemoryVersion(Base):
    """Retain immutable content references for conditional undo; no plaintext or content hash."""

    __tablename__ = "user_memory_versions"
    entry_id: Mapped[str] = mapped_column(
        ForeignKey("user_memory_entries.entry_id"), primary_key=True
    )
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    status: Mapped[str] = mapped_column(String(16))
    sensitive: Mapped[bool] = mapped_column(Boolean)
    body_uri: Mapped[str | None] = mapped_column(String(1024))
    memory_type: Mapped[str] = mapped_column(String(32))
    schema_version: Mapped[int] = mapped_column(Integer)


class UserMemoryDisclosure(Base):
    """Authorize one immutable memory version for one Run and recipient entry."""

    __tablename__ = "user_memory_disclosures"
    __table_args__ = (
        UniqueConstraint(
            "entry_id",
            "entry_version",
            "run_id",
            "recipient_entry",
            name="uq_memory_disclosure_recipient",
        ),
    )
    disclosure_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    entry_id: Mapped[str] = mapped_column(ForeignKey("user_memory_entries.entry_id"))
    entry_version: Mapped[int] = mapped_column(Integer)
    run_id: Mapped[str] = mapped_column(ForeignKey("llm_runs.run_id"), index=True)
    recipient_entry: Mapped[str] = mapped_column(String(64))
    allowed_fields_json: Mapped[list] = mapped_column(JSON)
    confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    request_key: Mapped[str] = mapped_column(String(128))


class UserMemoryShareGrant(TimestampMixin, Base):
    """Version-bound explicit sharing; field values remain in the original encrypted object."""

    __tablename__ = "user_memory_share_grants"
    __table_args__ = (
        UniqueConstraint(
            "entry_id", "target_owner_user_id", "target_entry", name="uq_memory_share_target"
        ),
    )
    grant_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    entry_id: Mapped[str] = mapped_column(ForeignKey("user_memory_entries.entry_id"))
    target_owner_user_id: Mapped[str] = mapped_column(ForeignKey("users.user_id"))
    target_entry: Mapped[str] = mapped_column(String(64))
    account_link_id: Mapped[str | None] = mapped_column(String(36))
    source_account_link_id: Mapped[str | None] = mapped_column(String(36))
    allowed_fields_json: Mapped[list] = mapped_column(JSON)
    confirmed_entry_version: Mapped[int] = mapped_column(Integer)
    allow_automatic_use: Mapped[bool] = mapped_column(Boolean, default=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, default=1)
