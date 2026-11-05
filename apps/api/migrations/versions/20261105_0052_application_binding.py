"""Pin caller connection fields when workflows are created; use the existing Alembic upgrade.

Existing caller workflows without a binding fail closed instead of adopting a new destination.
No credentials, content hashes, or full message text are stored in this table.
"""

import sqlalchemy as sa
from alembic import op

revision = "20261105_0052"
down_revision = "20261105_0051"
branch_labels = None
depends_on = None


def upgrade():
    """Add one immutable connection snapshot per caller Run without guessing old bindings."""
    op.create_table(
        "application_response_bindings",
        sa.Column("run_id", sa.String(36), sa.ForeignKey("llm_runs.run_id"), primary_key=True),
        sa.Column(
            "mapping_id",
            sa.String(36),
            sa.ForeignKey("application_user_mappings.mapping_id"),
            nullable=False,
        ),
        sa.Column("connection_json", sa.JSON(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )


def downgrade():
    """Refuse to erase active connection snapshots implicitly."""
    if (
        op.get_bind()
        .execute(sa.text("SELECT 1 FROM application_response_bindings LIMIT 1"))
        .first()
    ):
        raise RuntimeError("Export application bindings before downgrade")
    op.drop_table("application_response_bindings")
