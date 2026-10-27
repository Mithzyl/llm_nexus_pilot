"""Product memory metadata, separate from legacy experimental hash/text records."""

from sqlalchemy import JSON, Boolean, ForeignKey, Integer, String, UniqueConstraint
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


class UserMemoryOperation(TimestampMixin, Base):
    """Replay exact operations through encrypted request comparison, without content hashes."""

    __tablename__ = "user_memory_operations"
    operation_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    scope_id: Mapped[str] = mapped_column(ForeignKey("user_memory_scopes.scope_id"))
    request_key: Mapped[str] = mapped_column(String(128))
    request_uri: Mapped[str] = mapped_column(String(1024))
    result_json: Mapped[dict] = mapped_column(JSON)
    __table_args__ = (UniqueConstraint("scope_id", "request_key", name="uq_memory_operation_key"),)
