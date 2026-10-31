"""Persist whole-turn response requests/events; upgrade using the normal Alembic command."""

import sqlalchemy as sa
from alembic import op

revision = "20261029_0032"
down_revision = "20261029_0031"
branch_labels = None
depends_on = None


def upgrade():
    """Add response identity and event references without copying user prose into SQL."""
    op.create_table(
        "quick_response_executions",
        sa.Column("response_execution_id", sa.String(36), primary_key=True),
        sa.Column(
            "run_id", sa.String(36), sa.ForeignKey("llm_runs.run_id"), nullable=False, unique=True
        ),
        sa.Column("owner_user_id", sa.String(36), sa.ForeignKey("users.user_id"), nullable=False),
        sa.Column("request_key", sa.String(128), nullable=False),
        sa.Column("request_uri", sa.String(1024), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("assistant_message_id", sa.String(36)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("owner_user_id", "request_key", name="uq_quick_owner_key"),
    )
    op.create_table(
        "quick_response_events",
        sa.Column(
            "response_execution_id",
            sa.String(36),
            sa.ForeignKey("quick_response_executions.response_execution_id"),
            primary_key=True,
        ),
        sa.Column("sequence", sa.Integer(), primary_key=True),
        sa.Column("body_uri", sa.String(1024), nullable=False),
    )


def downgrade():
    """Remove only these response indexes on an explicitly requested downgrade."""
    op.drop_table("quick_response_events")
    op.drop_table("quick_response_executions")
