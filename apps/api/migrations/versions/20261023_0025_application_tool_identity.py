"""Add private application identity mappings; run with the existing Alembic upgrade command.

No applications, credentials or external users are seeded. Existing Runs stay native.
"""

import sqlalchemy as sa
from alembic import op

revision = "20261023_0025"
down_revision = "20261018_0024"
branch_labels = None
depends_on = None


def timestamps():
    """Return fresh timestamp columns matching the ORM's existing timestamp convention."""
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    ]


def upgrade():
    """Create additive identity tables without changing historical owners or tool records."""
    op.create_table(
        "applications",
        sa.Column("application_id", sa.String(64), primary_key=True),
        sa.Column("is_enabled", sa.Boolean(), nullable=False),
        *timestamps(),
    )
    op.create_table(
        "application_user_mappings",
        sa.Column("mapping_id", sa.String(36), primary_key=True),
        sa.Column(
            "application_id",
            sa.String(64),
            sa.ForeignKey("applications.application_id"),
            nullable=False,
        ),
        sa.Column(
            "external_user_id",
            sa.String(128).with_variant(sa.String(128, collation="utf8mb4_bin"), "mysql"),
            nullable=False,
        ),
        sa.Column(
            "pilot_principal_id",
            sa.String(128),
            sa.ForeignKey("users.user_id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("is_enabled", sa.Boolean(), nullable=False),
        *timestamps(),
        sa.UniqueConstraint(
            "application_id", "external_user_id", name="uq_application_external_user"
        ),
    )
    op.create_table(
        "run_application_context",
        sa.Column("run_id", sa.String(36), sa.ForeignKey("llm_runs.run_id"), primary_key=True),
        sa.Column(
            "mapping_id",
            sa.String(36),
            sa.ForeignKey("application_user_mappings.mapping_id"),
            nullable=False,
        ),
        sa.Column("agent_id", sa.String(64), nullable=False),
        sa.Column("allowed_tool_versions", sa.JSON(), nullable=False),
    )


def downgrade():
    """Remove application associations only; operators must export them before downgrade."""
    op.drop_table("run_application_context")
    op.drop_table("application_user_mappings")
    op.drop_table("applications")
