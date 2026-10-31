"""Add explicit memory field grants without granting access to existing entries."""

import sqlalchemy as sa
from alembic import op

revision = "20261029_0038"
down_revision = "20261029_0037"
branch_labels = None
depends_on = None


def upgrade():
    """Store only versioned permissions; values remain in original encrypted storage."""
    op.create_table(
        "user_memory_share_grants",
        sa.Column("grant_id", sa.String(36), primary_key=True),
        sa.Column(
            "entry_id", sa.String(36), sa.ForeignKey("user_memory_entries.entry_id"), nullable=False
        ),
        sa.Column(
            "target_owner_user_id", sa.String(128), sa.ForeignKey("users.user_id"), nullable=False
        ),
        sa.Column("target_entry", sa.String(64), nullable=False),
        sa.Column("account_link_id", sa.String(36)),
        sa.Column("allowed_fields_json", sa.JSON(), nullable=False),
        sa.Column("confirmed_entry_version", sa.Integer(), nullable=False),
        sa.Column("allow_automatic_use", sa.Boolean(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint(
            "entry_id", "target_owner_user_id", "target_entry", name="uq_memory_share_target"
        ),
    )


def downgrade():
    """Require explicit export before discarding any sharing decisions."""
    if (
        op.get_bind()
        .execute(sa.text("SELECT grant_id FROM user_memory_share_grants LIMIT 1"))
        .first()
    ):
        raise RuntimeError("Export memory sharing decisions before downgrade")
    op.drop_table("user_memory_share_grants")
