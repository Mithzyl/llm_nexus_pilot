"""Add a safe stable-prefix fingerprint to model invocation evidence.

Revision ID: 20260827_0014
Revises: 20260826_0013
Create Date: 2026-08-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260827_0014"
down_revision: str | None = "20260826_0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add an indexed non-reversible fingerprint for prompt-cache diagnostics."""

    op.add_column(
        "llm_attempts",
        sa.Column("prompt_prefix_fingerprint", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "ix_llm_attempts_prompt_prefix_fingerprint",
        "llm_attempts",
        ["prompt_prefix_fingerprint"],
    )


def downgrade() -> None:
    """Remove the prompt-prefix diagnostic without changing model attempt facts."""

    op.drop_index("ix_llm_attempts_prompt_prefix_fingerprint", table_name="llm_attempts")
    op.drop_column("llm_attempts", "prompt_prefix_fingerprint")
