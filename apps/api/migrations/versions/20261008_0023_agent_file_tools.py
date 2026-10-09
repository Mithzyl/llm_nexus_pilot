"""Persist versioned tool results; run with the existing API Alembic upgrade command.

Legacy rows remain nullable. No user files or model registrations are rewritten.
"""

import sqlalchemy as sa
from alembic import op

revision = "20261008_0023"
down_revision = "20260916_0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add call identity and result evidence without changing historical tool records."""
    with op.batch_alter_table("llm_tool_calls") as batch:
        batch.add_column(sa.Column("provider_tool_call_id", sa.String(255), nullable=True))
        batch.add_column(sa.Column("tool_version", sa.String(64), nullable=True))
        batch.add_column(sa.Column("output_json", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("error_code", sa.String(64), nullable=True))
        batch.create_unique_constraint(
            "uq_tool_attempt_provider_call", ["attempt_id", "provider_tool_call_id"]
        )


def downgrade() -> None:
    """Remove new tool evidence fields; operators must export results before downgrading."""
    with op.batch_alter_table("llm_tool_calls") as batch:
        batch.drop_constraint("uq_tool_attempt_provider_call", type_="unique")
        for name in ("error_code", "output_json", "tool_version", "provider_tool_call_id"):
            batch.drop_column(name)
