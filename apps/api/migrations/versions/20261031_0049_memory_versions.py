"""Retain encrypted memory version references; existing current versions are captured on mutation.

Run with the project's Alembic upgrade command. Does not rewrite MinIO objects or delete history.
"""

import sqlalchemy as sa
from alembic import op

revision = "20261031_0049"
down_revision = "20261030_0048"
branch_labels = None
depends_on = None


def upgrade():
    """Add immutable snapshots without guessing historical bodies that were not indexed."""
    op.create_table(
        "user_memory_versions",
        sa.Column(
            "entry_id",
            sa.String(36),
            sa.ForeignKey("user_memory_entries.entry_id"),
            primary_key=True,
        ),
        sa.Column("version", sa.Integer(), primary_key=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("sensitive", sa.Boolean(), nullable=False),
        sa.Column("body_uri", sa.String(1024)),
        sa.Column("memory_type", sa.String(32), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
    )


def downgrade():
    """Refuse to remove retained history until an operator explicitly exports it."""
    if op.get_bind().execute(sa.text("SELECT 1 FROM user_memory_versions LIMIT 1")).first():
        raise RuntimeError("Export memory versions before downgrade")
    op.drop_table("user_memory_versions")
