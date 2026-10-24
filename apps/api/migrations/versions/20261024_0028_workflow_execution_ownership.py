"""Add execution ownership; existing workflows remain owned by the inline implementation."""

import sqlalchemy as sa
from alembic import op

revision = "20261024_0028"
down_revision = "20261024_0027"
branch_labels = None
depends_on = None


def upgrade():
    """Add leases without enabling Worker execution or changing existing workflow outcomes."""
    op.add_column("llm_agent_workflow_executions", sa.Column(
        "execution_backend", sa.String(16), nullable=False, server_default="inline"))
    op.add_column("llm_agent_workflow_executions", sa.Column(
        "execution_owner", sa.String(128), nullable=True))
    op.add_column("llm_agent_workflow_executions", sa.Column(
        "execution_lease_version", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("llm_agent_workflow_executions", sa.Column(
        "execution_lease_expires_at", sa.DateTime(timezone=True), nullable=True))


def downgrade():
    """Remove ownership only during an explicitly approved rollback with workers stopped."""
    for column in ("execution_lease_expires_at", "execution_lease_version",
                   "execution_owner", "execution_backend"):
        op.drop_column("llm_agent_workflow_executions", column)
