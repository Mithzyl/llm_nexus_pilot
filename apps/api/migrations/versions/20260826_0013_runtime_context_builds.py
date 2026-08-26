"""Add Run-anchored, idempotent Context Builds and model-attempt lineage.

Revision ID: 20260826_0013
Revises: 20260820_0012
Create Date: 2026-08-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260826_0013"
down_revision: str | None = "20260820_0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add context anchors, replay identity, and Attempt-to-Context evidence."""

    op.add_column("llm_context_builds", sa.Column("run_id", sa.String(36), nullable=True))
    op.add_column(
        "llm_context_builds",
        sa.Column("current_user_message_id", sa.String(36), nullable=True),
    )
    op.add_column(
        "llm_context_builds", sa.Column("request_key", sa.String(128), nullable=True)
    )
    op.add_column(
        "llm_context_builds", sa.Column("request_hash", sa.String(64), nullable=True)
    )
    op.create_index("ix_llm_context_builds_run_id", "llm_context_builds", ["run_id"])
    op.create_index(
        "ix_llm_context_builds_current_user_message_id",
        "llm_context_builds",
        ["current_user_message_id"],
    )
    op.create_unique_constraint(
        "uq_context_build_request_key", "llm_context_builds", ["request_key"]
    )
    op.create_foreign_key(
        "fk_context_build_run_id",
        "llm_context_builds",
        "llm_runs",
        ["run_id"],
        ["run_id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_context_build_current_user_message_id",
        "llm_context_builds",
        "llm_messages",
        ["current_user_message_id"],
        ["message_id"],
        ondelete="RESTRICT",
    )

    op.add_column(
        "llm_attempts", sa.Column("context_build_id", sa.String(36), nullable=True)
    )
    op.create_index("ix_llm_attempts_context_build_id", "llm_attempts", ["context_build_id"])
    op.create_foreign_key(
        "fk_llm_attempts_context_build_id",
        "llm_attempts",
        "llm_context_builds",
        ["context_build_id"],
        ["context_build_id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    """Remove Attempt lineage before removing runtime Context Build columns."""

    op.drop_constraint(
        "fk_llm_attempts_context_build_id", "llm_attempts", type_="foreignkey"
    )
    op.drop_index("ix_llm_attempts_context_build_id", table_name="llm_attempts")
    op.drop_column("llm_attempts", "context_build_id")
    op.drop_constraint(
        "fk_context_build_current_user_message_id",
        "llm_context_builds",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_context_build_run_id", "llm_context_builds", type_="foreignkey"
    )
    op.drop_constraint(
        "uq_context_build_request_key", "llm_context_builds", type_="unique"
    )
    op.drop_index(
        "ix_llm_context_builds_current_user_message_id",
        table_name="llm_context_builds",
    )
    op.drop_index("ix_llm_context_builds_run_id", table_name="llm_context_builds")
    op.drop_column("llm_context_builds", "request_hash")
    op.drop_column("llm_context_builds", "request_key")
    op.drop_column("llm_context_builds", "current_user_message_id")
    op.drop_column("llm_context_builds", "run_id")
