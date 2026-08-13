"""Durable Agent workflow, node execution, and public event persistence models."""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from nexuspilot_api.models.base import Base, new_id, utc_now
from nexuspilot_api.models.enums import (
    AgentWorkflowNodeStatus,
    AgentWorkflowStatus,
)


class LlmAgentWorkflowExecution(Base):
    """Represent one Run executing one immutable Agent workflow definition version."""

    __tablename__ = "llm_agent_workflow_executions"
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            name="uq_agent_workflow_run",
        ),
        Index(
            "ix_agent_workflow_run_status_created_id",
            "run_id",
            "status",
            "created_at",
            "workflow_execution_id",
        ),
    )

    workflow_execution_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("llm_runs.run_id", ondelete="CASCADE"), index=True
    )
    workflow_name: Mapped[str] = mapped_column(String(64))
    workflow_version: Mapped[str] = mapped_column(String(32))
    execution_profile: Mapped[str] = mapped_column(String(64))
    status: Mapped[AgentWorkflowStatus] = mapped_column(
        Enum(AgentWorkflowStatus, native_enum=False, length=32),
        default=AgentWorkflowStatus.PENDING,
        index=True,
    )
    version: Mapped[int] = mapped_column(Integer, default=1)
    snapshot_version: Mapped[int] = mapped_column(Integer, default=1)
    current_stage: Mapped[str | None] = mapped_column(String(128))
    primary_node_execution_id: Mapped[str | None] = mapped_column(String(36), index=True)
    active_node_execution_ids_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    role_bindings_json: Mapped[dict] = mapped_column(JSON)
    request_json: Mapped[dict] = mapped_column(JSON)
    review_policy: Mapped[str] = mapped_column(String(32))
    idempotency_key: Mapped[str] = mapped_column(String(128))
    request_hash: Mapped[str] = mapped_column(String(64))
    max_nodes: Mapped[int] = mapped_column(Integer, default=32)
    max_model_calls: Mapped[int] = mapped_column(Integer, default=16)
    model_call_count: Mapped[int] = mapped_column(Integer, default=0)
    max_parallel_agents: Mapped[int] = mapped_column(Integer, default=1)
    wall_time_limit_ms: Mapped[int] = mapped_column(Integer, default=600_000)
    event_count: Mapped[int] = mapped_column(Integer, default=0)
    reserved_estimated_cost: Mapped[Decimal] = mapped_column(Numeric(12, 6), default=Decimal("0"))
    total_estimated_cost: Mapped[Decimal] = mapped_column(Numeric(12, 6), default=Decimal("0"))
    trace_id: Mapped[str | None] = mapped_column(String(32))
    error_code: Mapped[str | None] = mapped_column(String(128))
    error_json: Mapped[dict | None] = mapped_column(JSON)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        server_default=func.now(),
        onupdate=func.now(),
    )


class LlmAgentWorkflowNodeExecution(Base):
    """Store one typed orchestration node result without duplicating Attempt evidence."""

    __tablename__ = "llm_agent_workflow_node_executions"
    __table_args__ = (
        UniqueConstraint(
            "workflow_execution_id",
            "node_sequence",
            name="uq_agent_workflow_node_sequence",
        ),
        UniqueConstraint(
            "workflow_execution_id",
            "node_key",
            "node_attempt",
            name="uq_agent_workflow_node_key_attempt",
        ),
        Index(
            "ix_agent_workflow_node_workflow_status_sequence",
            "workflow_execution_id",
            "status",
            "node_sequence",
        ),
    )

    node_execution_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workflow_execution_id: Mapped[str] = mapped_column(
        ForeignKey("llm_agent_workflow_executions.workflow_execution_id", ondelete="CASCADE"),
        index=True,
    )
    run_id: Mapped[str] = mapped_column(
        ForeignKey("llm_runs.run_id", ondelete="CASCADE"), index=True
    )
    node_key: Mapped[str] = mapped_column(String(128))
    node_type: Mapped[str] = mapped_column(String(64))
    node_version: Mapped[str] = mapped_column(String(32), default="1.0.0")
    node_sequence: Mapped[int] = mapped_column(Integer)
    node_attempt: Mapped[int] = mapped_column(Integer, default=1)
    parent_node_execution_id: Mapped[str | None] = mapped_column(
        ForeignKey(
            "llm_agent_workflow_node_executions.node_execution_id",
            ondelete="SET NULL",
        ),
        index=True,
    )
    task_id: Mapped[str | None] = mapped_column(
        ForeignKey("llm_tasks.task_id", ondelete="SET NULL"), index=True
    )
    agent_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("llm_agent_runs.agent_run_id", ondelete="SET NULL"),
        index=True,
    )
    agent_turn_id: Mapped[str | None] = mapped_column(
        ForeignKey("llm_agent_turns.agent_turn_id", ondelete="SET NULL"),
        index=True,
    )
    agent_role: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[AgentWorkflowNodeStatus] = mapped_column(
        Enum(AgentWorkflowNodeStatus, native_enum=False, length=32),
        default=AgentWorkflowNodeStatus.PENDING,
        index=True,
    )
    input_schema_version: Mapped[str] = mapped_column(String(64))
    input_hash: Mapped[str] = mapped_column(String(64))
    input_json: Mapped[dict] = mapped_column(JSON)
    output_type: Mapped[str] = mapped_column(String(64))
    output_schema_version: Mapped[str] = mapped_column(String(64))
    output_json: Mapped[dict | None] = mapped_column(JSON)
    transition_json: Mapped[dict] = mapped_column(JSON, default=dict)
    evidence_json: Mapped[dict] = mapped_column(JSON, default=dict)
    usage_json: Mapped[dict] = mapped_column(JSON, default=dict)
    budget_json: Mapped[dict] = mapped_column(JSON, default=dict)
    public_view_json: Mapped[dict] = mapped_column(JSON, default=dict)
    warnings_json: Mapped[list] = mapped_column(JSON, default=list)
    error_code: Mapped[str | None] = mapped_column(String(128))
    error_json: Mapped[dict | None] = mapped_column(JSON)
    trace_id: Mapped[str | None] = mapped_column(String(32))
    span_id: Mapped[str | None] = mapped_column(String(16))
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )


class LlmAgentWorkflowEvent(Base):
    """Persist one ordered public workflow event for SSE replay and page refresh."""

    __tablename__ = "llm_agent_workflow_events"
    __table_args__ = (
        UniqueConstraint(
            "workflow_execution_id",
            "event_sequence",
            name="uq_agent_workflow_event_sequence",
        ),
        Index(
            "ix_agent_workflow_event_workflow_sequence",
            "workflow_execution_id",
            "event_sequence",
        ),
    )

    event_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workflow_execution_id: Mapped[str] = mapped_column(
        ForeignKey("llm_agent_workflow_executions.workflow_execution_id", ondelete="CASCADE"),
        index=True,
    )
    run_id: Mapped[str] = mapped_column(
        ForeignKey("llm_runs.run_id", ondelete="CASCADE"), index=True
    )
    node_execution_id: Mapped[str | None] = mapped_column(
        ForeignKey(
            "llm_agent_workflow_node_executions.node_execution_id",
            ondelete="SET NULL",
        ),
        index=True,
    )
    event_sequence: Mapped[int] = mapped_column(Integer)
    event_type: Mapped[str] = mapped_column(String(128), index=True)
    workflow_status: Mapped[str] = mapped_column(String(32))
    node_status: Mapped[str | None] = mapped_column(String(32))
    public_payload_json: Mapped[dict] = mapped_column(JSON)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )
    public_summary: Mapped[str] = mapped_column(Text)
    trace_id: Mapped[str | None] = mapped_column(String(32))
