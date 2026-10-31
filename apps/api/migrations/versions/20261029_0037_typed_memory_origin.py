"""Add memory type and immutable encryption-origin metadata; no ciphertext is rewritten."""

import sqlalchemy as sa
from alembic import op

revision = "20261029_0037"
down_revision = "20261029_0036"
branch_labels = None
depends_on = None


def upgrade():
    """Backfill known scope origins and keep historical App fields as free text compatibility."""
    bind = op.get_bind()
    missing = bind.execute(
        sa.text(
            "SELECT e.entry_id FROM user_memory_entries e LEFT JOIN user_memory_scopes s "
            "ON s.scope_id=e.scope_id WHERE s.scope_id IS NULL LIMIT 1"
        )
    ).first()
    if missing:
        raise RuntimeError("Memory origin scope is missing; resolve before migration")
    for column in (
        sa.Column("memory_type", sa.String(32), nullable=False, server_default="free_text"),
        sa.Column("schema_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("origin_owner_user_id", sa.String(128)),
        sa.Column("origin_entry", sa.String(64)),
        sa.Column("origin_scope_key", sa.String(128)),
    ):
        op.add_column("user_memory_entries", column)
    bind.execute(
        sa.text(
            "UPDATE user_memory_entries SET "
            "origin_owner_user_id=(SELECT s.owner_user_id FROM user_memory_scopes s "
            "WHERE s.scope_id=user_memory_entries.scope_id), "
            "origin_entry=(SELECT s.entry FROM user_memory_scopes s "
            "WHERE s.scope_id=user_memory_entries.scope_id), "
            "origin_scope_key=(SELECT s.scope_key FROM user_memory_scopes s "
            "WHERE s.scope_id=user_memory_entries.scope_id)"
        )
    )


def downgrade():
    """Refuse dropping metadata used by typed records; never alter encrypted objects."""
    if (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT entry_id FROM user_memory_entries WHERE memory_type != 'free_text' LIMIT 1"
            )
        )
        .first()
    ):
        raise RuntimeError("Typed memory must be exported before dropping its schema metadata")
    for name in (
        "origin_scope_key",
        "origin_entry",
        "origin_owner_user_id",
        "schema_version",
        "memory_type",
    ):
        op.drop_column("user_memory_entries", name)
