"""Add indexes for internal model-tool, Task-evaluation, and outbox queries.

Revision ID: 20260731_0006
Revises: 20260731_0005
Create Date: 2026-07-31
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260731_0006"
down_revision: str | None = "20260731_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create stable-order indexes for all Phase 1 internal audit lists."""

    op.create_index(
        "ix_tool_attempt_status_started_id",
        "llm_tool_calls",
        ["attempt_id", "status", "started_at", "tool_call_id"],
    )
    op.create_index(
        "ix_evaluation_run_type_created_id",
        "llm_evaluations",
        ["run_id", "evaluation_type", "created_at", "evaluation_id"],
    )
    op.create_index(
        "ix_evaluation_task_verdict_created_id",
        "llm_evaluations",
        ["task_id", "verdict", "created_at", "evaluation_id"],
    )
    op.create_index(
        "ix_outbox_status_created_id",
        "llm_outbox_events",
        ["status", "created_at", "event_id"],
    )
    op.create_index(
        "ix_outbox_aggregate_created_id",
        "llm_outbox_events",
        ["aggregate_type", "aggregate_id", "created_at", "event_id"],
    )


def downgrade() -> None:
    """Remove only the internal audit indexes introduced by this revision."""

    op.drop_index("ix_outbox_aggregate_created_id", table_name="llm_outbox_events")
    op.drop_index("ix_outbox_status_created_id", table_name="llm_outbox_events")
    op.drop_index(
        "ix_evaluation_task_verdict_created_id",
        table_name="llm_evaluations",
    )
    op.drop_index(
        "ix_evaluation_run_type_created_id",
        table_name="llm_evaluations",
    )
    op.drop_index("ix_tool_attempt_status_started_id", table_name="llm_tool_calls")
