"""Persist model-catalog and exact source evidence for stable Context previews.

Revision ID: 20260803_0009
Revises: 20260803_0008
Create Date: 2026-08-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260803_0009"
down_revision: str | None = "20260803_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add catalog limits, request controls, and immutable Context source snapshots."""

    op.add_column(
        "llm_context_builds",
        sa.Column("catalog_version_id", sa.String(36), nullable=True),
    )
    op.create_foreign_key(
        "fk_context_build_catalog_version",
        "llm_context_builds",
        "llm_model_catalog_versions",
        ["catalog_version_id"],
        ["catalog_version_id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_llm_context_builds_catalog_version_id",
        "llm_context_builds",
        ["catalog_version_id"],
    )
    op.add_column(
        "llm_context_builds",
        sa.Column(
            "reserved_output_tokens",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "llm_context_builds",
        sa.Column(
            "recent_message_count",
            sa.Integer(),
            nullable=False,
            server_default="12",
        ),
    )
    op.add_column(
        "llm_context_sources",
        sa.Column("message_role", sa.String(32), nullable=True),
    )
    op.add_column(
        "llm_context_sources",
        sa.Column("content_text", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    """Remove only the stable Context evidence fields introduced by this revision."""

    op.drop_column("llm_context_sources", "content_text")
    op.drop_column("llm_context_sources", "message_role")
    op.drop_column("llm_context_builds", "recent_message_count")
    op.drop_column("llm_context_builds", "reserved_output_tokens")
    op.drop_constraint(
        "fk_context_build_catalog_version",
        "llm_context_builds",
        type_="foreignkey",
    )
    op.drop_index(
        "ix_llm_context_builds_catalog_version_id",
        table_name="llm_context_builds",
    )
    op.drop_column("llm_context_builds", "catalog_version_id")
