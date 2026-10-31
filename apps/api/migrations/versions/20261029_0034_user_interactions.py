"""Add durable human input/approval requests without persisting question prose in SQL."""

import sqlalchemy as sa
from alembic import op

revision = "20261029_0034"
down_revision = "20261029_0033"
branch_labels = None
depends_on = None


def upgrade():
    """Create decision metadata and encrypted body references; existing executions are unchanged."""
    op.create_table(
        "user_interactions",
        sa.Column("interaction_id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("llm_runs.run_id"), nullable=False),
        sa.Column("owner_user_id", sa.String(36), sa.ForeignKey("users.user_id"), nullable=False),
        sa.Column(
            "tool_call_id",
            sa.String(36),
            sa.ForeignKey("llm_tool_calls.tool_call_id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("request_uri", sa.String(1024), nullable=False),
        sa.Column("response_uri", sa.String(1024)),
        sa.Column("reply_key", sa.String(128)),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_user_interactions_run_id", "user_interactions", ["run_id"])


def downgrade():
    """Remove interaction metadata only on explicitly requested downgrade."""
    op.drop_table("user_interactions")
