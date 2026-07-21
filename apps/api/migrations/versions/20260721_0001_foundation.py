"""Create phase-one business fact tables.

Revision ID: 20260721_0001
Revises: None
Create Date: 2026-07-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260721_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

run_status = sa.Enum(
    "PENDING",
    "RUNNING",
    "COMPLETED",
    "FAILED",
    "CANCELLED",
    name="runstatus",
    native_enum=False,
    length=32,
)
task_status = sa.Enum(
    "PENDING",
    "READY",
    "RUNNING",
    "WAITING_FOR_INPUT",
    "WAITING_FOR_APPROVAL",
    "WAITING_FOR_DEPENDENCY",
    "RETRY_SCHEDULED",
    "CANCEL_REQUESTED",
    "COMPLETED",
    "FAILED",
    "CANCELLED",
    name="taskstatus",
    native_enum=False,
    length=32,
)
attempt_status = sa.Enum(
    "STARTED",
    "COMPLETED",
    "FAILED",
    "TIMED_OUT",
    name="attemptstatus",
    native_enum=False,
    length=32,
)


def timestamp_columns() -> list[sa.Column]:
    """Return the standard mutable-record timestamps used by foundation tables."""

    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    ]


def upgrade() -> None:
    """Create phase-one tables, foreign keys, uniqueness rules, and query indexes."""

    op.create_table(
        "users",
        sa.Column("user_id", sa.String(128), primary_key=True),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        *timestamp_columns(),
    )
    op.create_table(
        "llm_runs",
        sa.Column("run_id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(128), sa.ForeignKey("users.user_id"), nullable=False),
        sa.Column("session_id", sa.String(128)),
        sa.Column("user_request", sa.Text(), nullable=False),
        sa.Column("run_type", sa.String(64), nullable=False),
        sa.Column("status", run_status, nullable=False),
        sa.Column("budget_limit", sa.Numeric(12, 6)),
        sa.Column("cost_used", sa.Numeric(12, 6), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        *timestamp_columns(),
    )
    op.create_index("ix_llm_runs_user_id", "llm_runs", ["user_id"])
    op.create_index("ix_llm_runs_session_id", "llm_runs", ["session_id"])
    op.create_index("ix_llm_runs_status", "llm_runs", ["status"])

    op.create_table(
        "llm_tasks",
        sa.Column("task_id", sa.String(36), primary_key=True),
        sa.Column(
            "run_id",
            sa.String(36),
            sa.ForeignKey("llm_runs.run_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "parent_task_id", sa.String(36), sa.ForeignKey("llm_tasks.task_id", ondelete="SET NULL")
        ),
        sa.Column("task_type", sa.String(64), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("objective", sa.Text(), nullable=False),
        sa.Column("assigned_role", sa.String(64)),
        sa.Column("status", task_status, nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("current_attempt", sa.Integer(), nullable=False),
        sa.Column("timeout_seconds", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        *timestamp_columns(),
    )
    op.create_index("ix_llm_tasks_run_id", "llm_tasks", ["run_id"])
    op.create_index("ix_llm_tasks_parent_task_id", "llm_tasks", ["parent_task_id"])
    op.create_index("ix_llm_tasks_status", "llm_tasks", ["status"])

    op.create_table(
        "llm_task_dependencies",
        sa.Column(
            "task_id",
            sa.String(36),
            sa.ForeignKey("llm_tasks.task_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "depends_on_task_id",
            sa.String(36),
            sa.ForeignKey("llm_tasks.task_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("dependency_type", sa.String(32), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("task_id", "depends_on_task_id"),
    )

    op.create_table(
        "llm_attempts",
        sa.Column("attempt_id", sa.String(36), primary_key=True),
        sa.Column(
            "run_id",
            sa.String(36),
            sa.ForeignKey("llm_runs.run_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "task_id", sa.String(36), sa.ForeignKey("llm_tasks.task_id", ondelete="SET NULL")
        ),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("model", sa.String(128), nullable=False),
        sa.Column("request_type", sa.String(64), nullable=False),
        sa.Column("status", attempt_status, nullable=False),
        sa.Column("input_tokens", sa.Integer()),
        sa.Column("output_tokens", sa.Integer()),
        sa.Column("cached_tokens", sa.Integer()),
        sa.Column("estimated_cost", sa.Numeric(12, 6)),
        sa.Column("latency_ms", sa.Integer()),
        sa.Column("provider_request_id", sa.String(255)),
        sa.Column("raw_request_uri", sa.String(1024)),
        sa.Column("raw_response_uri", sa.String(1024)),
        sa.Column("error_code", sa.String(128)),
        sa.Column("error_message", sa.Text()),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_attempt_run_task", "llm_attempts", ["run_id", "task_id"])

    op.create_table(
        "llm_tool_calls",
        sa.Column("tool_call_id", sa.String(36), primary_key=True),
        sa.Column(
            "attempt_id",
            sa.String(36),
            sa.ForeignKey("llm_attempts.attempt_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("tool_name", sa.String(128), nullable=False),
        sa.Column("risk_level", sa.String(32), nullable=False),
        sa.Column("input_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("permission_decision", sa.String(32)),
        sa.Column("result_uri", sa.String(1024)),
        sa.Column("error_message", sa.Text()),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_llm_tool_calls_attempt_id", "llm_tool_calls", ["attempt_id"])

    op.create_table(
        "llm_artifacts",
        sa.Column("artifact_id", sa.String(36), primary_key=True),
        sa.Column(
            "run_id",
            sa.String(36),
            sa.ForeignKey("llm_runs.run_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "task_id", sa.String(36), sa.ForeignKey("llm_tasks.task_id", ondelete="SET NULL")
        ),
        sa.Column("artifact_type", sa.String(64), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("mime_type", sa.String(255), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        # 512 utf8mb4 characters stay within InnoDB's indexed-key byte limit.
        sa.Column("storage_uri", sa.String(512), nullable=False, unique=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_llm_artifacts_run_id", "llm_artifacts", ["run_id"])
    op.create_index("ix_llm_artifacts_task_id", "llm_artifacts", ["task_id"])
    op.create_index("ix_llm_artifacts_content_hash", "llm_artifacts", ["content_hash"])

    op.create_table(
        "llm_evaluations",
        sa.Column("evaluation_id", sa.String(36), primary_key=True),
        sa.Column(
            "run_id",
            sa.String(36),
            sa.ForeignKey("llm_runs.run_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "task_id",
            sa.String(36),
            sa.ForeignKey("llm_tasks.task_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "candidate_attempt_id",
            sa.String(36),
            sa.ForeignKey("llm_attempts.attempt_id", ondelete="SET NULL"),
        ),
        sa.Column(
            "evaluator_attempt_id",
            sa.String(36),
            sa.ForeignKey("llm_attempts.attempt_id", ondelete="SET NULL"),
        ),
        sa.Column("evaluation_type", sa.String(64), nullable=False),
        sa.Column("verdict", sa.String(32), nullable=False),
        sa.Column("score", sa.Numeric(5, 2)),
        sa.Column("findings_json", sa.JSON(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_llm_evaluations_run_id", "llm_evaluations", ["run_id"])

    op.create_table(
        "llm_outbox_events",
        sa.Column("event_id", sa.String(36), primary_key=True),
        sa.Column("aggregate_type", sa.String(64), nullable=False),
        sa.Column("aggregate_id", sa.String(36), nullable=False),
        sa.Column("event_type", sa.String(128), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("publish_attempts", sa.Integer(), nullable=False),
        sa.Column("next_retry_at", sa.DateTime(timezone=True)),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_llm_outbox_events_aggregate_id", "llm_outbox_events", ["aggregate_id"])
    op.create_index("ix_llm_outbox_events_event_type", "llm_outbox_events", ["event_type"])
    op.create_index("ix_llm_outbox_events_status", "llm_outbox_events", ["status"])


def downgrade() -> None:
    """Drop phase-one tables in reverse dependency order."""

    for table_name in (
        "llm_outbox_events",
        "llm_evaluations",
        "llm_artifacts",
        "llm_tool_calls",
        "llm_attempts",
        "llm_task_dependencies",
        "llm_tasks",
        "llm_runs",
        "users",
    ):
        op.drop_table(table_name)
