"""L4 User Memory Profile and shared immutable MinIO snapshot registry models."""

from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
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

from nexuspilot_api.models.base import Base, new_id, utc_now
from nexuspilot_api.models.enums import SessionMemoryStatus, SnapshotObjectStatus


class LlmUserMemoryProfileSnapshot(Base):
    """Represent one immutable version of a User's core Memory Profile."""

    __tablename__ = "llm_user_memory_profile_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "version",
            name="uq_user_profile_version",
        ),
        UniqueConstraint(
            "user_id",
            "idempotency_key",
            name="uq_user_profile_key",
        ),
        Index(
            "ix_user_profile_user_status_version",
            "user_id",
            "status",
            "version",
        ),
    )

    user_memory_profile_snapshot_id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=new_id
    )
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.user_id", ondelete="CASCADE"),
        index=True,
    )
    schema_version: Mapped[str] = mapped_column(String(64), default="user_profile.v1")
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
    markdown_snapshot_object_id: Mapped[str | None] = mapped_column(String(36), index=True)
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


class LlmUserMemoryProfileItem(Base):
    """Link one User Profile version to the exact Memory facts it projected."""

    __tablename__ = "llm_user_memory_profile_items"
    __table_args__ = (
        UniqueConstraint(
            "user_memory_profile_snapshot_id",
            "memory_id",
            name="uq_user_profile_item_memory",
        ),
    )

    user_memory_profile_snapshot_id: Mapped[str] = mapped_column(
        ForeignKey(
            "llm_user_memory_profile_snapshots.user_memory_profile_snapshot_id",
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


class LlmMemorySnapshotObject(Base):
    """Register one immutable MinIO JSON or Markdown snapshot produced by L1-L4 Memory."""

    __tablename__ = "llm_memory_snapshot_objects"
    __table_args__ = (
        UniqueConstraint("storage_uri", name="uq_memory_snapshot_object_uri"),
        Index(
            "ix_memory_snapshot_object_layer_created",
            "memory_layer",
            "created_at",
            "memory_snapshot_object_id",
        ),
        Index(
            "ix_memory_snapshot_object_scope_created",
            "user_id",
            "session_id",
            "run_id",
            "task_id",
            "created_at",
        ),
        Index(
            "ix_memory_snapshot_object_status_created",
            "status",
            "created_at",
            "memory_snapshot_object_id",
        ),
    )

    memory_snapshot_object_id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=new_id
    )
    memory_layer: Mapped[str] = mapped_column(String(16))
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.user_id", ondelete="CASCADE"),
        index=True,
    )
    project_id: Mapped[str | None] = mapped_column(
        ForeignKey("llm_projects.project_id", ondelete="RESTRICT"),
        index=True,
    )
    session_id: Mapped[str | None] = mapped_column(
        ForeignKey("llm_sessions.session_id", ondelete="RESTRICT"),
        index=True,
    )
    run_id: Mapped[str | None] = mapped_column(
        ForeignKey("llm_runs.run_id", ondelete="RESTRICT"),
        index=True,
    )
    task_id: Mapped[str | None] = mapped_column(
        ForeignKey("llm_tasks.task_id", ondelete="RESTRICT"),
        index=True,
    )
    object_type: Mapped[str] = mapped_column(String(64))
    schema_version: Mapped[str] = mapped_column(String(64))
    object_version: Mapped[int] = mapped_column(Integer)
    storage_uri: Mapped[str] = mapped_column(String(1024))
    content_hash: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    mime_type: Mapped[str] = mapped_column(String(128))
    status: Mapped[SnapshotObjectStatus] = mapped_column(
        Enum(SnapshotObjectStatus, native_enum=False, length=32),
        default=SnapshotObjectStatus.ACTIVE,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        server_default=func.now(),
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cleanup_error: Mapped[str | None] = mapped_column(String(512))
