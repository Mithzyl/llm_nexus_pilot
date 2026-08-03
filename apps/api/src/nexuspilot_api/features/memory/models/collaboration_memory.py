"""L2 Collaboration Memory: Agent Handoff, Run Snapshot, and Memory Packet models."""

from datetime import datetime

from sqlalchemy import (
    JSON,
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
from nexuspilot_api.models.enums import (
    HandoffStatus,
    MemoryPacketItemType,
    SessionMemoryStatus,
)


class LlmAgentHandoff(Base):
    """Represent one immutable handoff fact submitted by an Agent coordinator."""

    __tablename__ = "llm_agent_handoffs"
    __table_args__ = (
        UniqueConstraint(
            "agent_run_id",
            "idempotency_key",
            name="uq_agent_handoff_key",
        ),
        Index(
            "ix_agent_handoff_run_task_created",
            "run_id",
            "task_id",
            "created_at",
            "agent_handoff_id",
        ),
        Index(
            "ix_agent_handoff_agent_run_created",
            "agent_run_id",
            "created_at",
            "agent_handoff_id",
        ),
    )

    agent_handoff_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    agent_run_id: Mapped[str] = mapped_column(
        ForeignKey("llm_agent_runs.agent_run_id", ondelete="RESTRICT"),
        index=True,
    )
    run_id: Mapped[str] = mapped_column(
        ForeignKey("llm_runs.run_id", ondelete="CASCADE"),
        index=True,
    )
    task_id: Mapped[str | None] = mapped_column(
        ForeignKey("llm_tasks.task_id", ondelete="CASCADE"),
        index=True,
    )
    schema_version: Mapped[str] = mapped_column(String(64), default="agent_handoff.v1")
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[HandoffStatus] = mapped_column(
        Enum(HandoffStatus, native_enum=False, length=32),
        default=HandoffStatus.COMPLETED,
    )
    handoff_json: Mapped[dict] = mapped_column(JSON)
    supersedes_handoff_id: Mapped[str | None] = mapped_column(
        ForeignKey("llm_agent_handoffs.agent_handoff_id", ondelete="SET NULL"),
        index=True,
    )
    json_snapshot_object_id: Mapped[str | None] = mapped_column(String(36), index=True)
    markdown_snapshot_object_id: Mapped[str | None] = mapped_column(String(36), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(128))
    request_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        server_default=func.now(),
    )


class LlmRunMemorySnapshot(Base):
    """Represent one immutable merged version of completed Agent Handoffs."""

    __tablename__ = "llm_run_memory_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "version",
            name="uq_run_memory_snapshot_version",
        ),
        UniqueConstraint(
            "run_id",
            "idempotency_key",
            name="uq_run_memory_snapshot_key",
        ),
        Index(
            "ix_run_memory_snapshot_run_status_version",
            "run_id",
            "status",
            "version",
        ),
    )

    run_memory_snapshot_id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=new_id
    )
    run_id: Mapped[str] = mapped_column(
        ForeignKey("llm_runs.run_id", ondelete="CASCADE"),
        index=True,
    )
    schema_version: Mapped[str] = mapped_column(String(64), default="run_memory_state.v1")
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[SessionMemoryStatus] = mapped_column(
        Enum(SessionMemoryStatus, native_enum=False, length=32),
        default=SessionMemoryStatus.GENERATING,
    )
    state_json: Mapped[dict] = mapped_column(JSON)
    previous_snapshot_id: Mapped[str | None] = mapped_column(String(36))
    expected_previous_version: Mapped[int | None] = mapped_column(Integer)
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


class LlmRunMemorySnapshotHandoff(Base):
    """Link one Run Memory Snapshot to the exact Handoff versions it merged."""

    __tablename__ = "llm_run_memory_snapshot_handoffs"
    __table_args__ = (
        UniqueConstraint(
            "run_memory_snapshot_id",
            "agent_handoff_id",
            name="uq_run_snapshot_handoff",
        ),
    )

    run_memory_snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("llm_run_memory_snapshots.run_memory_snapshot_id", ondelete="CASCADE"),
        primary_key=True,
    )
    agent_handoff_id: Mapped[str] = mapped_column(
        ForeignKey("llm_agent_handoffs.agent_handoff_id", ondelete="CASCADE"),
        primary_key=True,
    )
    handoff_order: Mapped[int] = mapped_column(Integer)


class LlmMemoryPacket(Base):
    """Represent one immutable, version-pinned input package given to a model call."""

    __tablename__ = "llm_memory_packets"
    __table_args__ = (
        Index(
            "ix_memory_packet_run_agent_created",
            "run_id",
            "agent_run_id",
            "created_at",
            "memory_packet_id",
        ),
        Index("ix_memory_packet_user_created", "user_id", "created_at"),
    )

    memory_packet_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    agent_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("llm_agent_runs.agent_run_id", ondelete="SET NULL"),
        index=True,
    )
    run_id: Mapped[str | None] = mapped_column(
        ForeignKey("llm_runs.run_id", ondelete="SET NULL"),
        index=True,
    )
    task_id: Mapped[str | None] = mapped_column(
        ForeignKey("llm_tasks.task_id", ondelete="SET NULL"),
        index=True,
    )
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.user_id", ondelete="CASCADE"),
        index=True,
    )
    schema_version: Mapped[str] = mapped_column(String(64), default="agent_memory_packet.v1")
    packet_json: Mapped[dict] = mapped_column(JSON)
    user_profile_snapshot_id: Mapped[str | None] = mapped_column(String(36))
    project_memory_profile_snapshot_id: Mapped[str | None] = mapped_column(String(36))
    session_state_id: Mapped[str | None] = mapped_column(String(36))
    session_summary_id: Mapped[str | None] = mapped_column(String(36))
    run_memory_snapshot_id: Mapped[str | None] = mapped_column(String(36))
    agent_working_state_version_id: Mapped[str | None] = mapped_column(String(36))
    estimated_token_count: Mapped[int] = mapped_column(Integer)
    tokenizer_name: Mapped[str] = mapped_column(String(64))
    tokenizer_version: Mapped[str] = mapped_column(String(64))
    json_snapshot_object_id: Mapped[str | None] = mapped_column(String(36), index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        server_default=func.now(),
    )


class LlmMemoryPacketItem(Base):
    """Represent one frozen resource reference selected into a Memory Packet."""

    __tablename__ = "llm_memory_packet_items"
    __table_args__ = (
        UniqueConstraint(
            "memory_packet_id",
            "item_order",
            name="uq_memory_packet_item_order",
        ),
        Index("ix_memory_packet_item_resource", "item_type", "resource_id"),
    )

    memory_packet_id: Mapped[str] = mapped_column(
        ForeignKey("llm_memory_packets.memory_packet_id", ondelete="CASCADE"),
        primary_key=True,
    )
    item_type: Mapped[MemoryPacketItemType] = mapped_column(
        Enum(MemoryPacketItemType, native_enum=False, length=32),
        primary_key=True,
    )
    resource_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    resource_version: Mapped[str | None] = mapped_column(String(64))
    item_order: Mapped[int] = mapped_column(Integer)
    selected_token_count: Mapped[int] = mapped_column(Integer)
    content_hash: Mapped[str] = mapped_column(String(64))
