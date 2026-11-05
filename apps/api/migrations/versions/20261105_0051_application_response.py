"""Track caller-processed model responses; upgrade with the existing Alembic workflow.

Full requests/results remain in object storage. Nonempty execution history blocks downgrade.
"""

import sqlalchemy as sa
from alembic import op

revision = "20261105_0051"
down_revision = "20261031_0050"
branch_labels = None
depends_on = None


def upgrade():
    """Add stable response processing records without modifying old workflow data."""
    op.create_table(
        "application_response_executions",
        sa.Column("execution_id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("llm_runs.run_id"), nullable=False),
        sa.Column(
            "response_id",
            sa.String(36),
            sa.ForeignKey("llm_attempts.attempt_id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("request_uri", sa.String(1024), nullable=False),
        sa.Column("result_uri", sa.String(1024)),
        sa.Column("destination", sa.String(2048), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index(
        "ix_application_response_executions_run_id", "application_response_executions", ["run_id"]
    )


def downgrade():
    """Do not silently discard business execution evidence."""
    if (
        op.get_bind()
        .execute(sa.text("SELECT 1 FROM application_response_executions LIMIT 1"))
        .first()
    ):
        raise RuntimeError("Export application executions before downgrade")
    op.drop_table("application_response_executions")
