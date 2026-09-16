"""Add private upload reservations; upgrade with make api-migrate before enabling uploads.

Revision ID: 20260916_0022
Revises: 20260912_0021
Downgrade removes metadata only; delete private objects through the file service first.
"""

import sqlalchemy as sa
from alembic import op

revision = "20260916_0022"
down_revision = "20260912_0021"
branch_labels = None
depends_on = None


def upgrade():
    """Create immutable upload metadata and durable cleanup keys without changing old tables."""

    op.create_table(
        "user_files",
        sa.Column("file_id", sa.String(36), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(128),
            sa.ForeignKey("users.user_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("key_hash", sa.String(64), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("mime_type", sa.String(255), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("storage_uri", sa.String(512), nullable=False),
        sa.Column("parsed_uri", sa.String(512), nullable=False),
        sa.Column("parsed_hash", sa.String(64)),
        sa.Column("parsed_size", sa.BigInteger()),
        sa.Column("parse_summary", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("error", sa.String(255)),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("llm_runs.run_id", ondelete="RESTRICT")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "key_hash", name="uq_user_file_request"),
    )
    op.create_index("ix_user_file_owner_status", "user_files", ["user_id", "status"])


def downgrade():
    """Remove only upload metadata; operators must clean stored bytes before downgrade."""

    op.drop_table("user_files")
