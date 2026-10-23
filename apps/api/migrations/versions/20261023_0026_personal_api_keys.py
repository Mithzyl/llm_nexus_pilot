"""Add personal key metadata and irreversible verifiers; no keys or owners are seeded.

Apply using make api-migrate. Existing users, sessions and Runs are unchanged.
"""

import sqlalchemy as sa
from alembic import op

revision = "20261023_0026"
down_revision = "20261023_0025"
branch_labels = None
depends_on = None


def upgrade():
    """Create revocable personal credentials without storing plaintext or Provider secrets."""
    op.create_table(
        "user_api_keys",
        sa.Column("api_key_id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(128), sa.ForeignKey("users.user_id"), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("verifier", sa.String(64), nullable=False),
        sa.Column("masked_key", sa.String(80), nullable=False),
        sa.Column("scopes", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_user_api_keys_user_id", "user_api_keys", ["user_id"])


def downgrade():
    """Remove credentials only after operators deliberately revoke/export their metadata."""
    op.drop_index("ix_user_api_keys_user_id", table_name="user_api_keys")
    op.drop_table("user_api_keys")
