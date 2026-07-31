"""Add composite indexes for Run and Task cursor queries.

Revision ID: 20260731_0004
Revises: 20260731_0003
Create Date: 2026-07-31
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260731_0004"
down_revision: str | None = "20260731_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create indexes matching Run and Task ownership, status, and cursor filters."""

    op.create_index("ix_llm_runs_created", "llm_runs", ["created_at", "run_id"])
    op.create_index(
        "ix_llm_runs_user_status_created",
        "llm_runs",
        ["user_id", "status", "created_at", "run_id"],
    )
    op.create_index(
        "ix_llm_runs_session_status_created",
        "llm_runs",
        ["session_id", "status", "created_at", "run_id"],
    )
    op.create_index("ix_llm_tasks_created", "llm_tasks", ["created_at", "task_id"])
    op.create_index(
        "ix_llm_tasks_run_status_created",
        "llm_tasks",
        ["run_id", "status", "created_at", "task_id"],
    )
    op.create_index(
        "ix_llm_tasks_type_status_created",
        "llm_tasks",
        ["task_type", "status", "created_at", "task_id"],
    )
    op.create_index(
        "ix_llm_tasks_role_status_created",
        "llm_tasks",
        ["assigned_role", "status", "created_at", "task_id"],
    )


def downgrade() -> None:
    """Remove Run and Task composite query indexes in reverse creation order."""

    op.drop_index("ix_llm_tasks_role_status_created", table_name="llm_tasks")
    op.drop_index("ix_llm_tasks_type_status_created", table_name="llm_tasks")
    op.drop_index("ix_llm_tasks_run_status_created", table_name="llm_tasks")
    op.drop_index("ix_llm_tasks_created", table_name="llm_tasks")
    op.drop_index("ix_llm_runs_session_status_created", table_name="llm_runs")
    op.drop_index("ix_llm_runs_user_status_created", table_name="llm_runs")
    op.drop_index("ix_llm_runs_created", table_name="llm_runs")
