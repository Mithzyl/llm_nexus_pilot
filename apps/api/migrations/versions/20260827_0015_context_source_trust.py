"""Add context policy and per-source trust evidence.

Revision ID: 20260827_0015
Revises: 20260827_0014
Create Date: 2026-08-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260827_0015"
down_revision: str | None = "20260827_0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Persist the policy, trust classification, and required-source decision."""

    op.add_column(
        "llm_context_builds",
        sa.Column(
            "policy_version",
            sa.String(length=64),
            nullable=False,
            server_default="context_policy.v2",
        ),
    )
    op.add_column(
        "llm_context_sources",
        sa.Column(
            "trust_level",
            sa.String(length=32),
            nullable=False,
            server_default="legacy_unclassified",
        ),
    )
    op.add_column(
        "llm_context_sources",
        sa.Column("is_required", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    """Remove context policy evidence without changing stored source contents."""

    op.drop_column("llm_context_sources", "is_required")
    op.drop_column("llm_context_sources", "trust_level")
    op.drop_column("llm_context_builds", "policy_version")
