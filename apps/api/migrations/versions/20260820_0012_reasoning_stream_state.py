"""Add public reasoning replay state and private provider continuation metadata.

Revision ID: 20260820_0012
Revises: 20260813_0011
Create Date: 2026-08-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260820_0012"
down_revision: str | None = "20260813_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create reasoning event, snapshot, continuation, accounting, and message lineage data."""

    op.add_column("llm_attempts", sa.Column("reasoning_tokens", sa.Integer(), nullable=True))
    op.add_column(
        "llm_attempts",
        sa.Column(
            "reasoning_display_policy",
            sa.String(length=32),
            nullable=False,
            server_default="hidden",
        ),
    )
    op.add_column(
        "llm_attempts",
        sa.Column("first_visible_token_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "llm_messages",
        sa.Column("source_model_attempt_id", sa.String(length=36), nullable=True),
    )
    op.create_index(
        "ix_llm_messages_source_model_attempt_id",
        "llm_messages",
        ["source_model_attempt_id"],
    )
    op.create_foreign_key(
        "fk_llm_messages_source_model_attempt_id",
        "llm_messages",
        "llm_attempts",
        ["source_model_attempt_id"],
        ["attempt_id"],
        ondelete="SET NULL",
    )

    op.create_table(
        "llm_model_response_events",
        sa.Column("event_id", sa.String(length=36), nullable=False),
        sa.Column("attempt_id", sa.String(length=36), nullable=False),
        sa.Column("event_sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("reasoning_block_id", sa.String(length=128), nullable=True),
        sa.Column("public_payload_json", sa.JSON(), nullable=False),
        sa.Column(
            "schema_version",
            sa.String(length=64),
            nullable=False,
            server_default="conversation-stream.v2",
        ),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["attempt_id"], ["llm_attempts.attempt_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("event_id"),
        sa.UniqueConstraint("attempt_id", "event_sequence"),
    )
    op.create_index(
        "ix_model_response_event_attempt_sequence",
        "llm_model_response_events",
        ["attempt_id", "event_sequence"],
    )

    op.create_table(
        "llm_reasoning_blocks",
        sa.Column("reasoning_block_id", sa.String(length=128), nullable=False),
        sa.Column("attempt_id", sa.String(length=36), nullable=False),
        sa.Column("block_index", sa.Integer(), nullable=False),
        sa.Column("presentation_kind", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("visible_text", sa.Text(), nullable=True),
        sa.Column("reasoning_tokens", sa.Integer(), nullable=True),
        sa.Column("display_policy", sa.String(length=32), nullable=False),
        sa.Column("final_event_sequence", sa.Integer(), nullable=True),
        sa.Column("snapshot_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("first_visible_token_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["attempt_id"], ["llm_attempts.attempt_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("reasoning_block_id"),
        sa.UniqueConstraint("attempt_id", "block_index"),
    )
    op.create_index(
        "ix_reasoning_block_attempt_index",
        "llm_reasoning_blocks",
        ["attempt_id", "block_index"],
    )

    op.create_table(
        "llm_provider_continuation_states",
        sa.Column("continuation_state_id", sa.String(length=36), nullable=False),
        sa.Column("parent_attempt_id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=True),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("state_kind", sa.String(length=64), nullable=False),
        sa.Column("encrypted_payload_uri", sa.String(length=1024), nullable=False),
        sa.Column("encryption_key_version", sa.String(length=32), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["parent_attempt_id"], ["llm_attempts.attempt_id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["run_id"], ["llm_runs.run_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("continuation_state_id"),
        sa.UniqueConstraint("parent_attempt_id"),
    )
    op.create_index(
        "ix_provider_continuation_run_expiry",
        "llm_provider_continuation_states",
        ["run_id", "expires_at"],
    )


def downgrade() -> None:
    """Remove reasoning state after dropping dependent constraints and indexes."""

    op.drop_table("llm_provider_continuation_states")
    op.drop_table("llm_reasoning_blocks")
    op.drop_table("llm_model_response_events")
    op.drop_constraint(
        "fk_llm_messages_source_model_attempt_id",
        "llm_messages",
        type_="foreignkey",
    )
    op.drop_index("ix_llm_messages_source_model_attempt_id", table_name="llm_messages")
    op.drop_column("llm_messages", "source_model_attempt_id")
    op.drop_column("llm_attempts", "first_visible_token_at")
    op.drop_column("llm_attempts", "reasoning_display_policy")
    op.drop_column("llm_attempts", "reasoning_tokens")
