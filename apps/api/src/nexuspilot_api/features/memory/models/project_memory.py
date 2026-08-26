"""L3 Project scope, workspace locators, and Project Memory Profile models."""

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from nexuspilot_api.models.base import Base, TimestampMixin, new_id, utc_now
from nexuspilot_api.models.enums import ProjectMemoryStatus, ProjectStatus, SessionMemoryStatus


class LlmProject(TimestampMixin, Base):
    """Represent the minimal explicit Project scope that can opt into Memory."""

    __tablename__ = "llm_projects"
    __table_args__ = (
        Index(
            "ix_project_owner_status_created",
            "owner_user_id",
            "status",
            "created_at",
            "project_id",
        ),
    )

    project_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    owner_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.user_id", ondelete="CASCADE"),
        index=True,
    )
    project_name: Mapped[str] = mapped_column(String(255))
    status: Mapped[ProjectStatus] = mapped_column(
        Enum(ProjectStatus, native_enum=False, length=32),
        default=ProjectStatus.ACTIVE,
        index=True,
    )
    memory_status: Mapped[ProjectMemoryStatus] = mapped_column(
        Enum(ProjectMemoryStatus, native_enum=False, length=32),
        default=ProjectMemoryStatus.DISABLED,
        index=True,
    )
    memory_enabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    memory_enabled_by_actor_id: Mapped[str | None] = mapped_column(String(128))
    current_memory_profile_snapshot_id: Mapped[str | None] = mapped_column(String(36), index=True)


class LlmProjectWorkspace(TimestampMixin, Base):
    """Represent one credential-free canonical locator bound to a Project."""

    __tablename__ = "llm_project_workspaces"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "workspace_type",
            "locator_hash",
            name="uq_project_workspace_locator",
        ),
        Index("ix_project_workspace_project_active", "project_id", "is_active"),
    )

    project_workspace_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("llm_projects.project_id", ondelete="CASCADE"),
        index=True,
    )
    workspace_type: Mapped[str] = mapped_column(String(64))
    credential_free_locator: Mapped[str] = mapped_column(String(1024))
    locator_hash: Mapped[str] = mapped_column(String(64))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class LlmProjectMemoryProfileSnapshot(Base):
    """Represent one immutable version of a Project's core Memory Profile."""

    __tablename__ = "llm_project_memory_profile_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "version",
            name="uq_project_profile_version",
        ),
        UniqueConstraint(
            "project_id",
            "idempotency_key",
            name="uq_project_profile_key",
        ),
        Index(
            "ix_project_profile_project_status_version",
            "project_id",
            "status",
            "version",
        ),
        Index(
            "ix_project_profile_markdown_snapshot_object",
            "markdown_snapshot_object_id",
        ),
    )

    project_memory_profile_snapshot_id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=new_id
    )
    project_id: Mapped[str] = mapped_column(
        ForeignKey("llm_projects.project_id", ondelete="CASCADE"),
        index=True,
    )
    schema_version: Mapped[str] = mapped_column(String(64), default="project_profile.v1")
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[SessionMemoryStatus] = mapped_column(
        Enum(SessionMemoryStatus, native_enum=False, length=32),
        default=SessionMemoryStatus.GENERATING,
    )
    profile_json: Mapped[dict] = mapped_column(JSON)
    estimated_token_count: Mapped[int] = mapped_column(Integer)
    tokenizer_name: Mapped[str] = mapped_column(String(64))
    tokenizer_version: Mapped[str] = mapped_column(String(64))
    previous_snapshot_id: Mapped[str | None] = mapped_column(String(36))
    json_snapshot_object_id: Mapped[str | None] = mapped_column(String(36), index=True)
    markdown_snapshot_object_id: Mapped[str | None] = mapped_column(String(36))
    idempotency_key: Mapped[str] = mapped_column(String(128))
    request_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        server_default=func.now(),
    )
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(128))


class LlmProjectMemoryProfileItem(Base):
    """Link one Project Profile version to the exact Memory facts it projected."""

    __tablename__ = "llm_project_memory_profile_items"
    __table_args__ = (
        UniqueConstraint(
            "project_memory_profile_snapshot_id",
            "memory_id",
            name="uq_project_profile_item_memory",
        ),
    )

    project_memory_profile_snapshot_id: Mapped[str] = mapped_column(
        ForeignKey(
            "llm_project_memory_profile_snapshots.project_memory_profile_snapshot_id",
            ondelete="CASCADE",
        ),
        primary_key=True,
    )
    memory_id: Mapped[str] = mapped_column(
        ForeignKey("llm_memories.memory_id", ondelete="CASCADE"),
        primary_key=True,
    )
    memory_version_id: Mapped[str] = mapped_column(
        ForeignKey("llm_memory_versions.memory_version_id", ondelete="CASCADE"),
        index=True,
    )
    field_path: Mapped[str] = mapped_column(String(128))
    item_order: Mapped[int] = mapped_column(Integer)
    content_hash: Mapped[str] = mapped_column(String(64))
