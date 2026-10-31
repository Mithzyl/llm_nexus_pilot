"""Persist only safe discovery outcome metadata, not credentials or remote response bodies."""

import sqlalchemy as sa
from alembic import op

revision = "20261030_0045"
down_revision = "20261030_0044"
branch_labels = None
depends_on = None


def upgrade():
    """Leave existing connections unverified until an explicit discovery request succeeds."""
    op.add_column("mcp_user_connections", sa.Column("discovery_status", sa.String(64)))
    op.add_column("mcp_user_connections", sa.Column("discovered_at", sa.DateTime(timezone=True)))


def downgrade():
    """Require export when discovery outcomes exist before removing their metadata."""
    if (
        op.get_bind()
        .execute(
            sa.text("SELECT 1 FROM mcp_user_connections WHERE discovered_at IS NOT NULL LIMIT 1")
        )
        .first()
    ):
        raise RuntimeError("Export MCP discovery outcomes before downgrade")
    op.drop_column("mcp_user_connections", "discovered_at")
    op.drop_column("mcp_user_connections", "discovery_status")
