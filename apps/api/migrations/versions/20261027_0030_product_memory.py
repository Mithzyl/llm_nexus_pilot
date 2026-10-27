"""Add scoped product memory metadata without activating or rewriting legacy memory.

Apply with the project's Alembic upgrade after 0029. Content remains encrypted in
private object storage; rollback removes metadata only and requires explicit approval.
"""

import sqlalchemy as sa
from alembic import op

revision = "20261027_0030"
down_revision = "20261024_0029"
branch_labels = None
depends_on = None


def timestamps():
    """Create separate timestamp columns for each new metadata table."""
    return [
        sa.Column(name, sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())
        for name in ("created_at", "updated_at")
    ]


def upgrade():
    """Add isolated scope, entry, operation and authentication-derived Run permission records."""
    op.create_table(
        "user_memory_scopes",
        sa.Column("scope_id", sa.String(36), primary_key=True),
        sa.Column("owner_user_id", sa.String(128), sa.ForeignKey("users.user_id"), nullable=False),
        sa.Column("entry", sa.String(64), nullable=False),
        sa.Column("scope_key", sa.String(128), nullable=False),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("llm_projects.project_id")),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        *timestamps(),
        sa.UniqueConstraint("owner_user_id", "scope_key", name="uq_memory_scope_owner"),
    )
    op.create_table(
        "user_memory_entries",
        sa.Column("entry_id", sa.String(36), primary_key=True),
        sa.Column(
            "scope_id", sa.String(36), sa.ForeignKey("user_memory_scopes.scope_id"), nullable=False
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("sensitive", sa.Boolean(), nullable=False),
        sa.Column("body_uri", sa.String(1024)),
        sa.Column("source_run_id", sa.String(36), sa.ForeignKey("llm_runs.run_id")),
        sa.Column("disclosure_run_id", sa.String(36), sa.ForeignKey("llm_runs.run_id")),
        *timestamps(),
    )
    op.create_index("ix_user_memory_entries_scope_id", "user_memory_entries", ["scope_id"])
    op.create_table(
        "user_memory_operations",
        sa.Column("operation_id", sa.String(36), primary_key=True),
        sa.Column(
            "scope_id", sa.String(36), sa.ForeignKey("user_memory_scopes.scope_id"), nullable=False
        ),
        sa.Column("request_key", sa.String(128), nullable=False),
        sa.Column("request_uri", sa.String(1024), nullable=False),
        sa.Column("result_json", sa.JSON(), nullable=False),
        *timestamps(),
        sa.UniqueConstraint("scope_id", "request_key", name="uq_memory_operation_key"),
    )
    op.create_table(
        "memory_run_permissions",
        sa.Column("run_id", sa.String(36), sa.ForeignKey("llm_runs.run_id"), primary_key=True),
        sa.Column("allowed", sa.Boolean(), nullable=False),
    )


def downgrade():
    """Remove this migration's metadata, never object bytes or legacy memory records."""
    op.drop_table("memory_run_permissions")
    op.drop_table("user_memory_operations")
    # MySQL needs the scope index until its FK disappears with this table.
    op.drop_table("user_memory_entries")
    op.drop_table("user_memory_scopes")
