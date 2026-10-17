"""Add body references without rewriting historical payloads; upgrade before starting API.

Existing SQL bodies remain readable. Backfill uses the explicit maintenance command,
not this schema migration. Downgrade refuses while object-backed rows remain.
"""

import sqlalchemy as sa
from alembic import op

revision = "20261018_0024"
down_revision = "20261008_0023"
branch_labels = None
depends_on = None

TABLES = (
    "llm_messages",
    "llm_runs",
    "llm_tasks",
    "llm_context_sources",
    "llm_tool_calls",
    "llm_agent_workflow_executions",
    "llm_agent_workflow_node_executions",
    "llm_agent_workflow_events",
    "llm_model_response_events",
    "llm_reasoning_blocks",
    "llm_agent_handoffs",
    "llm_run_memory_snapshots",
    "llm_memory_packets",
)


def upgrade() -> None:
    """Add nullable references so old records need no data rewrite to remain readable."""
    for table in TABLES:
        op.add_column(table, sa.Column("body_ref_json", sa.JSON(), nullable=True))


def downgrade() -> None:
    """Refuse to drop object references until an explicit restore has rehydrated SQL bodies."""
    bind = op.get_bind()
    for table in TABLES:
        if bind.execute(
            sa.text(f"SELECT 1 FROM {table} WHERE body_ref_json IS NOT NULL LIMIT 1")
        ).first():
            raise RuntimeError("Restore object-backed bodies before downgrading")
    for table in reversed(TABLES):
        op.drop_column(table, "body_ref_json")
