"""Add replaceable quick-response leases; existing accepted requests retain their identity."""

import sqlalchemy as sa
from alembic import op

revision = "20261029_0033"
down_revision = "20261029_0032"
branch_labels = None
depends_on = None


def upgrade():
    """Allow a new Worker to inspect expired executions without redispatching unknown effects."""
    op.add_column("quick_response_executions", sa.Column("execution_owner", sa.String(128)))
    op.add_column(
        "quick_response_executions",
        sa.Column("lease_version", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "quick_response_executions", sa.Column("lease_expires_at", sa.DateTime(timezone=True))
    )


def downgrade():
    """Remove lease metadata only on explicit downgrade; response records remain."""
    for name in ("lease_expires_at", "lease_version", "execution_owner"):
        op.drop_column("quick_response_executions", name)
