"""Store per-user MCP restrictions, never private connection addresses or credentials."""

import sqlalchemy as sa
from alembic import op

revision = "20261030_0044"
down_revision = "20261030_0043"
branch_labels = None
depends_on = None


def upgrade():
    """Keep legacy private approvals unchanged until the user creates a restriction."""
    op.create_table(
        "mcp_user_connections",
        sa.Column(
            "owner_user_id", sa.String(128), sa.ForeignKey("users.user_id"), primary_key=True
        ),
        sa.Column("connection_alias", sa.String(64), primary_key=True),
        sa.Column("config_version", sa.String(32), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("approved_tools_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade():
    """Require export before removing user restrictions, avoiding accidental re-enablement."""
    if op.get_bind().execute(sa.text("SELECT 1 FROM mcp_user_connections LIMIT 1")).first():
        raise RuntimeError("Export MCP restrictions before downgrade")
    op.drop_table("mcp_user_connections")
