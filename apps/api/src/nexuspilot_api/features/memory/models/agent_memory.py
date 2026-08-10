"""Agent Run, Agent Turn, and L0 Agent Working Memory persistence models."""

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
    AgentRunStatus,
    AgentTurnStatus,
    AgentTurnType,
    AgentWorkingStateStatus,
)


class LlmAgentRun(Base):
    """Represent one role-owned execution of one Task inside a Run."""

    __tablename__ = "llm_agent_runs"
    __table_args__ = (
        UniqueConstraint("task_id", "agent_role", name="uq_agent_run_task_role"),
        Index(
            "ix_agent_run_run_status_created_id",
            "run_id",
            "status",
            "created_at",
            "agent_run_id",
        ),
        Index(
            "ix_agent_run_task_status_created_id",
            "task_id",
            "status",
            "created_at",
            "agent_run_id",
        ),
    )

    agent_run_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workflow_execution_id: Mapped[str | None] = mapped_column(
        ForeignKey(
            "llm_agent_workflow_executions.workflow_execution_id",
            ondelete="SET NULL",
        ),
        index=True,
    )
    run_id: Mapped[str] = mapped_column(
        ForeignKey("llm_runs.run_id", ondelete="CASCADE"),
        index=True,
    )
    task_id: Mapped[str] = mapped_column(
        ForeignKey("llm_tasks.task_id", ondelete="CASCADE"),
        index=True,
    )
    agent_role: Mapped[str] = mapped_column(String(64))
    status: Mapped[AgentRunStatus] = mapped_column(
        Enum(AgentRunStatus, native_enum=False, length=32),
        default=AgentRunStatus.PENDING,
        index=True,
    )
    provider: Mapped[str | None] = mapped_column(String(64))
    model: Mapped[str | None] = mapped_column(String(128))
    current_working_state_version_id: Mapped[str | None] = mapped_column(String(36), index=True)
    current_turn_sequence: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        server_default=func.now(),
    )


class LlmAgentTurn(Base):
    """Represent one model/tool loop step inside an Agent Run."""

    __tablename__ = "llm_agent_turns"
    __table_args__ = (
        UniqueConstraint(
            "agent_run_id",
            "turn_sequence",
            name="uq_agent_turn_sequence",
        ),
        Index("ix_agent_turn_run_sequence", "agent_run_id", "turn_sequence"),
        Index("ix_agent_turn_attempt", "model_attempt_id"),
    )

    agent_turn_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    node_execution_id: Mapped[str | None] = mapped_column(
        ForeignKey(
            "llm_agent_workflow_node_executions.node_execution_id",
            ondelete="SET NULL",
        ),
        index=True,
    )
    agent_run_id: Mapped[str] = mapped_column(
        ForeignKey("llm_agent_runs.agent_run_id", ondelete="CASCADE"),
        index=True,
    )
    turn_sequence: Mapped[int] = mapped_column(Integer)
    turn_type: Mapped[AgentTurnType] = mapped_column(
        Enum(AgentTurnType, native_enum=False, length=32)
    )
    status: Mapped[AgentTurnStatus] = mapped_column(
        Enum(AgentTurnStatus, native_enum=False, length=32),
        default=AgentTurnStatus.STARTED,
    )
    model_attempt_id: Mapped[str | None] = mapped_column(
        ForeignKey("llm_attempts.attempt_id", ondelete="SET NULL"),
        index=True,
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        server_default=func.now(),
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        server_default=func.now(),
    )


class LlmAgentWorkingStateVersion(Base):
    """Represent one immutable L0 check point version for an Agent Run."""

    __tablename__ = "llm_agent_working_state_versions"
    __table_args__ = (
        UniqueConstraint(
            "agent_run_id",
            "version",
            name="uq_agent_working_state_version",
        ),
        UniqueConstraint(
            "agent_run_id",
            "idempotency_key",
            name="uq_agent_working_state_key",
        ),
        Index(
            "ix_agent_working_state_run_status_version",
            "agent_run_id",
            "status",
            "version",
        ),
    )

    agent_working_state_version_id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=new_id
    )
    agent_run_id: Mapped[str] = mapped_column(
        ForeignKey("llm_agent_runs.agent_run_id", ondelete="CASCADE"),
        index=True,
    )
    agent_turn_id: Mapped[str | None] = mapped_column(
        ForeignKey("llm_agent_turns.agent_turn_id", ondelete="SET NULL"),
        index=True,
    )
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[AgentWorkingStateStatus] = mapped_column(
        Enum(AgentWorkingStateStatus, native_enum=False, length=32),
        default=AgentWorkingStateStatus.ACTIVE,
    )
    state_json: Mapped[dict] = mapped_column(JSON)
    previous_version_id: Mapped[str | None] = mapped_column(String(36))
    estimated_token_count: Mapped[int] = mapped_column(Integer)
    tokenizer_name: Mapped[str] = mapped_column(String(64))
    tokenizer_version: Mapped[str] = mapped_column(String(64))
    idempotency_key: Mapped[str] = mapped_column(String(128))
    request_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    content_erased_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        server_default=func.now(),
    )
