"""Add durable Agent workflow cost reservations.

Revision ID: 20260813_0011
Revises: 20260810_0010
Create Date: 2026-08-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260813_0011"
down_revision: str | None = "20260810_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Store the sum reserved by in-flight model calls for one workflow."""

    op.add_column(
        "llm_agent_workflow_executions",
        sa.Column(
            "reserved_estimated_cost",
            sa.Numeric(12, 6),
            nullable=False,
            server_default="0",
        ),
    )


def downgrade() -> None:
    """Remove Agent workflow cost reservations."""

    op.drop_column(
        "llm_agent_workflow_executions",
        "reserved_estimated_cost",
    )
