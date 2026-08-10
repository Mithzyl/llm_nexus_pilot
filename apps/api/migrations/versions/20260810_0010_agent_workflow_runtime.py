"""Add durable Agent workflow executions, complete nodes, and replayable events.

Revision ID: 20260810_0010
Revises: 20260803_0009
Create Date: 2026-08-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260810_0010"
down_revision: str | None = "20260803_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _enum(*values: str, name: str) -> sa.Enum:
    """Build a native-enum-free VARCHAR enum matching the ORM convention."""

    return sa.Enum(*values, name=name, native_enum=False, length=32)


workflow_status = _enum(
    "PENDING",
    "RUNNING",
    "WAITING_FOR_INPUT",
    "COMPLETED",
    "FAILED",
    "CANCELLED",
    "OUTCOME_UNKNOWN",
    name="agentworkflowstatus",
)
node_status = _enum(
    "PENDING",
    "RUNNING",
    "COMPLETED",
    "SKIPPED",
    "BLOCKED",
    "FAILED",
    "CANCELLED",
    "OUTCOME_UNKNOWN",
    name="agentworkflownodestatus",
)


def upgrade() -> None:
    """Create workflow facts, then link existing Agent Run and Turn records."""

    op.create_table(
        "llm_agent_workflow_executions",
        sa.Column("workflow_execution_id", sa.String(36), primary_key=True),
        sa.Column(
            "run_id",
            sa.String(36),
            sa.ForeignKey("llm_runs.run_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("workflow_name", sa.String(64), nullable=False),
        sa.Column("workflow_version", sa.String(32), nullable=False),
        sa.Column("execution_profile", sa.String(64), nullable=False),
        sa.Column("status", workflow_status, nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("snapshot_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("current_stage", sa.String(128)),
        sa.Column("primary_node_execution_id", sa.String(36)),
        sa.Column("active_node_execution_ids_json", sa.JSON(), nullable=False),
        sa.Column("role_bindings_json", sa.JSON(), nullable=False),
        sa.Column("request_json", sa.JSON(), nullable=False),
        sa.Column("review_policy", sa.String(32), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("max_nodes", sa.Integer(), nullable=False, server_default="32"),
        sa.Column("max_model_calls", sa.Integer(), nullable=False, server_default="16"),
        sa.Column("model_call_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_parallel_agents", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("wall_time_limit_ms", sa.Integer(), nullable=False, server_default="600000"),
        sa.Column("event_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "total_estimated_cost",
            sa.Numeric(12, 6),
            nullable=False,
            server_default="0",
        ),
        sa.Column("trace_id", sa.String(32)),
        sa.Column("error_code", sa.String(128)),
        sa.Column("error_json", sa.JSON()),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint(
            "run_id",
            name="uq_agent_workflow_run",
        ),
    )
    op.create_index(
        "ix_llm_agent_workflow_executions_run_id",
        "llm_agent_workflow_executions",
        ["run_id"],
    )
    op.create_index(
        "ix_llm_agent_workflow_executions_status",
        "llm_agent_workflow_executions",
        ["status"],
    )
    op.create_index(
        "ix_llm_agent_workflow_executions_primary_node_execution_id",
        "llm_agent_workflow_executions",
        ["primary_node_execution_id"],
    )
    op.create_index(
        "ix_agent_workflow_run_status_created_id",
        "llm_agent_workflow_executions",
        ["run_id", "status", "created_at", "workflow_execution_id"],
    )

    op.create_table(
        "llm_agent_workflow_node_executions",
        sa.Column("node_execution_id", sa.String(36), primary_key=True),
        sa.Column(
            "workflow_execution_id",
            sa.String(36),
            sa.ForeignKey(
                "llm_agent_workflow_executions.workflow_execution_id",
                ondelete="CASCADE",
            ),
            nullable=False,
        ),
        sa.Column(
            "run_id",
            sa.String(36),
            sa.ForeignKey("llm_runs.run_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("node_key", sa.String(128), nullable=False),
        sa.Column("node_type", sa.String(64), nullable=False),
        sa.Column("node_version", sa.String(32), nullable=False),
        sa.Column("node_sequence", sa.Integer(), nullable=False),
        sa.Column("node_attempt", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "parent_node_execution_id",
            sa.String(36),
            sa.ForeignKey(
                "llm_agent_workflow_node_executions.node_execution_id",
                ondelete="SET NULL",
            ),
        ),
        sa.Column(
            "task_id",
            sa.String(36),
            sa.ForeignKey("llm_tasks.task_id", ondelete="SET NULL"),
        ),
        sa.Column(
            "agent_run_id",
            sa.String(36),
            sa.ForeignKey("llm_agent_runs.agent_run_id", ondelete="SET NULL"),
        ),
        sa.Column(
            "agent_turn_id",
            sa.String(36),
            sa.ForeignKey("llm_agent_turns.agent_turn_id", ondelete="SET NULL"),
        ),
        sa.Column("agent_role", sa.String(64)),
        sa.Column("status", node_status, nullable=False),
        sa.Column("input_schema_version", sa.String(64), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("input_json", sa.JSON(), nullable=False),
        sa.Column("output_type", sa.String(64), nullable=False),
        sa.Column("output_schema_version", sa.String(64), nullable=False),
        sa.Column("output_json", sa.JSON()),
        sa.Column("transition_json", sa.JSON(), nullable=False),
        sa.Column("evidence_json", sa.JSON(), nullable=False),
        sa.Column("usage_json", sa.JSON(), nullable=False),
        sa.Column("budget_json", sa.JSON(), nullable=False),
        sa.Column("public_view_json", sa.JSON(), nullable=False),
        sa.Column("warnings_json", sa.JSON(), nullable=False),
        sa.Column("error_code", sa.String(128)),
        sa.Column("error_json", sa.JSON()),
        sa.Column("trace_id", sa.String(32)),
        sa.Column("span_id", sa.String(16)),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("duration_ms", sa.Integer()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint(
            "workflow_execution_id",
            "node_sequence",
            name="uq_agent_workflow_node_sequence",
        ),
        sa.UniqueConstraint(
            "workflow_execution_id",
            "node_key",
            "node_attempt",
            name="uq_agent_workflow_node_key_attempt",
        ),
    )
    for column_name in [
        "workflow_execution_id",
        "run_id",
        "parent_node_execution_id",
        "task_id",
        "agent_run_id",
        "agent_turn_id",
        "status",
    ]:
        op.create_index(
            f"ix_llm_agent_workflow_node_executions_{column_name}",
            "llm_agent_workflow_node_executions",
            [column_name],
        )
    op.create_index(
        "ix_agent_workflow_node_workflow_status_sequence",
        "llm_agent_workflow_node_executions",
        ["workflow_execution_id", "status", "node_sequence"],
    )

    op.create_table(
        "llm_agent_workflow_events",
        sa.Column("event_id", sa.String(36), primary_key=True),
        sa.Column(
            "workflow_execution_id",
            sa.String(36),
            sa.ForeignKey(
                "llm_agent_workflow_executions.workflow_execution_id",
                ondelete="CASCADE",
            ),
            nullable=False,
        ),
        sa.Column(
            "run_id",
            sa.String(36),
            sa.ForeignKey("llm_runs.run_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "node_execution_id",
            sa.String(36),
            sa.ForeignKey(
                "llm_agent_workflow_node_executions.node_execution_id",
                ondelete="SET NULL",
            ),
        ),
        sa.Column("event_sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(128), nullable=False),
        sa.Column("workflow_status", sa.String(32), nullable=False),
        sa.Column("node_status", sa.String(32)),
        sa.Column("public_payload_json", sa.JSON(), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("public_summary", sa.Text(), nullable=False),
        sa.Column("trace_id", sa.String(32)),
        sa.UniqueConstraint(
            "workflow_execution_id",
            "event_sequence",
            name="uq_agent_workflow_event_sequence",
        ),
    )
    for column_name in [
        "workflow_execution_id",
        "run_id",
        "node_execution_id",
        "event_type",
    ]:
        op.create_index(
            f"ix_llm_agent_workflow_events_{column_name}",
            "llm_agent_workflow_events",
            [column_name],
        )
    op.create_index(
        "ix_agent_workflow_event_workflow_sequence",
        "llm_agent_workflow_events",
        ["workflow_execution_id", "event_sequence"],
    )

    op.add_column(
        "llm_agent_runs",
        sa.Column("workflow_execution_id", sa.String(36), nullable=True),
    )
    op.create_foreign_key(
        "fk_agent_run_workflow_execution",
        "llm_agent_runs",
        "llm_agent_workflow_executions",
        ["workflow_execution_id"],
        ["workflow_execution_id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_llm_agent_runs_workflow_execution_id",
        "llm_agent_runs",
        ["workflow_execution_id"],
    )
    op.add_column(
        "llm_agent_turns",
        sa.Column("node_execution_id", sa.String(36), nullable=True),
    )
    op.create_foreign_key(
        "fk_agent_turn_node_execution",
        "llm_agent_turns",
        "llm_agent_workflow_node_executions",
        ["node_execution_id"],
        ["node_execution_id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_llm_agent_turns_node_execution_id",
        "llm_agent_turns",
        ["node_execution_id"],
    )


def downgrade() -> None:
    """Remove Agent links before dropping workflow event, node, and execution facts."""

    op.drop_index("ix_llm_agent_turns_node_execution_id", table_name="llm_agent_turns")
    op.drop_constraint("fk_agent_turn_node_execution", "llm_agent_turns", type_="foreignkey")
    op.drop_column("llm_agent_turns", "node_execution_id")
    op.drop_index("ix_llm_agent_runs_workflow_execution_id", table_name="llm_agent_runs")
    op.drop_constraint("fk_agent_run_workflow_execution", "llm_agent_runs", type_="foreignkey")
    op.drop_column("llm_agent_runs", "workflow_execution_id")
    op.drop_table("llm_agent_workflow_events")
    op.drop_table("llm_agent_workflow_node_executions")
    op.drop_table("llm_agent_workflow_executions")
