"""Add model response idempotency and physical retry evidence.

Revision ID: 20260723_0002
Revises: 20260721_0001
Create Date: 2026-07-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260723_0002"
down_revision: str | None = "20260721_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add logical request keys, retry counts, and per-HTTP-attempt records."""

    with op.batch_alter_table("llm_attempts") as batch:
        batch.add_column(sa.Column("request_key", sa.String(128), nullable=True))
        batch.add_column(sa.Column("retry_count", sa.Integer(), server_default="0", nullable=False))
        batch.create_unique_constraint("uq_llm_attempts_request_key", ["request_key"])

    op.create_table(
        "llm_attempt_retries",
        sa.Column("retry_id", sa.String(36), primary_key=True),
        sa.Column(
            "attempt_id",
            sa.String(36),
            sa.ForeignKey("llm_attempts.attempt_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("attempt_index", sa.Integer(), nullable=False),
        sa.Column("status_code", sa.Integer()),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("error_type", sa.String(128)),
        sa.Column("error_message", sa.Text()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("attempt_id", "attempt_index", name="uq_attempt_retry_index"),
    )
    op.create_index(
        "ix_llm_attempt_retries_attempt_id",
        "llm_attempt_retries",
        ["attempt_id"],
    )


def downgrade() -> None:
    """Remove physical retry evidence and response idempotency fields."""

    op.drop_table("llm_attempt_retries")
    with op.batch_alter_table("llm_attempts") as batch:
        batch.drop_constraint("uq_llm_attempts_request_key", type_="unique")
        batch.drop_column("retry_count")
        batch.drop_column("request_key")
