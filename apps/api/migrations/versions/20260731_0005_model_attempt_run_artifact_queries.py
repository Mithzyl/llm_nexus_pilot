"""Add indexes for bounded Attempt and Artifact cursor queries.

Revision ID: 20260731_0005
Revises: 20260731_0004
Create Date: 2026-07-31
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260731_0005"
down_revision: str | None = "20260731_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create owner-first composite indexes for the new stable query orders."""

    op.create_index(
        "ix_attempt_run_status_started_id",
        "llm_attempts",
        ["run_id", "status", "started_at", "attempt_id"],
    )
    op.create_index(
        "ix_attempt_task_status_started_id",
        "llm_attempts",
        ["task_id", "status", "started_at", "attempt_id"],
    )
    op.create_index(
        "ix_artifact_run_type_created_id",
        "llm_artifacts",
        ["run_id", "artifact_type", "created_at", "artifact_id"],
    )
    op.create_index(
        "ix_artifact_task_type_created_id",
        "llm_artifacts",
        ["task_id", "artifact_type", "created_at", "artifact_id"],
    )


def downgrade() -> None:
    """Remove only the composite indexes introduced by this revision."""

    op.drop_index("ix_artifact_task_type_created_id", table_name="llm_artifacts")
    op.drop_index("ix_artifact_run_type_created_id", table_name="llm_artifacts")
    op.drop_index("ix_attempt_task_status_started_id", table_name="llm_attempts")
    op.drop_index("ix_attempt_run_status_started_id", table_name="llm_attempts")
