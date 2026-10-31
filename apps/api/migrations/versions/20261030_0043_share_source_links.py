"""Track the source App association when both memory endpoints have separate owners."""

import sqlalchemy as sa
from alembic import op

revision = "20261030_0043"
down_revision = "20261030_0042"
branch_labels = None
depends_on = None


def upgrade():
    """Keep existing same-owner grants unchanged; new App-to-App grants record both links."""
    op.add_column("user_memory_share_grants", sa.Column("source_account_link_id", sa.String(36)))


def downgrade():
    """Reject removal while any grant still depends on a source association."""
    if (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT 1 FROM user_memory_share_grants "
                "WHERE source_account_link_id IS NOT NULL LIMIT 1"
            )
        )
        .first()
    ):
        raise RuntimeError("Export linked sharing grants before downgrade")
    op.drop_column("user_memory_share_grants", "source_account_link_id")
