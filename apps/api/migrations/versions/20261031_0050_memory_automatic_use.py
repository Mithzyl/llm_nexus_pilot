"""Store explicit native-use consent separately from memory ownership and account linking."""

import sqlalchemy as sa
from alembic import op

revision = "20261031_0050"
down_revision = "20261031_0049"
branch_labels = None
depends_on = None


def upgrade():
    """Existing scopes start without cross-entry automatic-use consent."""
    op.add_column("user_memory_scopes", sa.Column("native_use_grant", sa.JSON(), nullable=True))


def downgrade():
    """Require export of stored consent before removing this field."""
    if (
        op.get_bind()
        .execute(
            sa.text("SELECT 1 FROM user_memory_scopes WHERE native_use_grant IS NOT NULL LIMIT 1")
        )
        .first()
    ):
        raise RuntimeError("Export memory consent before downgrade")
    op.drop_column("user_memory_scopes", "native_use_grant")
