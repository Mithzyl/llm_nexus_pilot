"""Add session-scoped turn retry records without rewriting existing conversations.

Revision ID: 20260912_0021
Revises: 20260905_0020
Run ``make api-migrate`` before clients submit keyed turns. No historical keys are
invented. Downgrade removes retry records only; Run and Message facts remain.
"""

import sqlalchemy as sa
from alembic import op

revision = "20260912_0021"
down_revision = "20260905_0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create the retry map in the same database transaction domain as Run and Message."""

    op.create_table(
        "llm_conversation_turns",
        sa.Column("session_id", sa.String(36), nullable=False),
        sa.Column("key_hash", sa.String(64), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("run_id", sa.String(36), nullable=False),
        sa.Column("message_id", sa.String(36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("session_id", "key_hash"),
        sa.ForeignKeyConstraint(["session_id"], ["llm_sessions.session_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["run_id"], ["llm_runs.run_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["message_id"], ["llm_messages.message_id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("run_id", name="uq_llm_conversation_turn_run"),
        sa.UniqueConstraint("message_id", name="uq_llm_conversation_turn_message"),
    )


def downgrade() -> None:
    """Remove only the added retry map; rollback loses keyed replay, not messages or Runs."""

    op.drop_table("llm_conversation_turns")
