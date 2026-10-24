"""Add project-owned immutable file copies; do not move or delete existing user objects.

Apply through the normal Alembic upgrade after 0028. Local directory export remains opt-in.
"""

import sqlalchemy as sa
from alembic import op

revision = "20261024_0029"
down_revision = "20261024_0028"
branch_labels = None
depends_on = None


def upgrade():
    """Create structured file publication metadata without changing Memory or source files."""
    op.create_table(
        "project_workspace_files",
        sa.Column("project_file_id", sa.String(36), primary_key=True),
        sa.Column(
            "project_id",
            sa.String(36),
            sa.ForeignKey("llm_projects.project_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("source_type", sa.String(16), nullable=False),
        sa.Column("source_id", sa.String(36), nullable=False),
        sa.Column("key_hash", sa.String(64), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("storage_uri", sa.String(512), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("mime_type", sa.String(255), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("project_id", "filename", name="uq_project_file_name"),
        sa.UniqueConstraint("project_id", "key_hash", name="uq_project_file_request"),
    )
    op.create_index(
        "ix_project_workspace_files_project_id", "project_workspace_files", ["project_id"]
    )


def downgrade():
    """Remove metadata only after rollback approval; never delete stored bytes."""
    op.drop_index("ix_project_workspace_files_project_id", table_name="project_workspace_files")
    op.drop_table("project_workspace_files")
