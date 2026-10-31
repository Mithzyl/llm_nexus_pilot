"""Persist independent MCP aliases and immutable approval metadata without content hashes."""

import sqlalchemy as sa
from alembic import op

revision = "20261030_0046"
down_revision = "20261030_0045"
branch_labels = None
depends_on = None


def upgrade():
    """Create new aliases on demand; legacy bridge calls and their stored records stay valid."""
    op.create_table(
        "mcp_tool_bindings",
        sa.Column("tool_alias", sa.String(64), primary_key=True),
        sa.Column("owner_user_id", sa.String(128), sa.ForeignKey("users.user_id"), nullable=False),
        sa.Column("connection_alias", sa.String(64), nullable=False),
        sa.Column("config_version", sa.String(32), nullable=False),
        sa.Column("permission_revision", sa.Integer(), nullable=False),
        sa.Column("remote_tool_name", sa.String(128), nullable=False),
        sa.Column("definition_json", sa.JSON(), nullable=False),
        sa.UniqueConstraint(
            "owner_user_id",
            "connection_alias",
            "config_version",
            "permission_revision",
            "remote_tool_name",
            name="uq_mcp_tool_approval",
        ),
    )


def downgrade():
    """Protect aliases referenced by saved tool executions from silent removal."""
    if op.get_bind().execute(sa.text("SELECT 1 FROM mcp_tool_bindings LIMIT 1")).first():
        raise RuntimeError("Export MCP tool bindings before downgrade")
    op.drop_table("mcp_tool_bindings")
