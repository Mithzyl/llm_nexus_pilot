"""Persist normalized model cache diagnostics and Agent role grouping.

Revision ID: 20260829_0018
Revises: 20260828_0017
Create Date: 2026-08-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260829_0018"
down_revision: str | None = "20260828_0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add prompt-cache write, miss, and Agent role facts to model Attempts."""

    op.add_column(
        "llm_attempts",
        sa.Column("agent_role", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "llm_attempts",
        sa.Column("cache_write_tokens", sa.Integer(), nullable=True),
    )
    op.add_column(
        "llm_attempts",
        sa.Column("cache_miss_tokens", sa.Integer(), nullable=True),
    )
    op.create_index("ix_llm_attempts_agent_role", "llm_attempts", ["agent_role"])


def downgrade() -> None:
    """Remove model cache diagnostics while preserving the existing cached-token field."""

    op.drop_index("ix_llm_attempts_agent_role", table_name="llm_attempts")
    op.drop_column("llm_attempts", "cache_miss_tokens")
    op.drop_column("llm_attempts", "cache_write_tokens")
    op.drop_column("llm_attempts", "agent_role")
