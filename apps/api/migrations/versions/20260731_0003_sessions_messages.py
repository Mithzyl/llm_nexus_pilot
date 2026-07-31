"""Add durable conversation sessions and immutable ordered messages.

Revision ID: 20260731_0003
Revises: 20260723_0002
Create Date: 2026-07-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260731_0003"
down_revision: str | None = "20260723_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

session_status = sa.Enum(
    "ACTIVE",
    "ARCHIVED",
    name="sessionstatus",
    native_enum=False,
    length=32,
)
message_role = sa.Enum(
    "SYSTEM",
    "USER",
    "ASSISTANT",
    "TOOL",
    name="messagerole",
    native_enum=False,
    length=32,
)


def upgrade() -> None:
    """Create conversation tables, ownership keys, ordering constraints, and indexes."""

    op.create_table(
        "llm_sessions",
        sa.Column("session_id", sa.String(36), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(128),
            sa.ForeignKey("users.user_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.String(255)),
        sa.Column("status", session_status, nullable=False),
        sa.Column("next_message_sequence", sa.Integer(), server_default="1", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_llm_sessions_user_id", "llm_sessions", ["user_id"])
    op.create_index("ix_llm_sessions_status", "llm_sessions", ["status"])
    op.create_index(
        "ix_llm_sessions_user_status_created",
        "llm_sessions",
        ["user_id", "status", "created_at", "session_id"],
    )

    op.create_table(
        "llm_messages",
        sa.Column("message_id", sa.String(36), primary_key=True),
        sa.Column(
            "session_id",
            sa.String(36),
            sa.ForeignKey("llm_sessions.session_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "run_id",
            sa.String(36),
            sa.ForeignKey("llm_runs.run_id", ondelete="SET NULL"),
        ),
        sa.Column(
            "parent_message_id",
            sa.String(36),
            sa.ForeignKey("llm_messages.message_id", ondelete="SET NULL"),
        ),
        sa.Column("role", message_role, nullable=False),
        sa.Column("content_type", sa.String(64), nullable=False),
        sa.Column("content_text", sa.Text()),
        sa.Column("content_uri", sa.String(512)),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("token_count", sa.Integer()),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "session_id",
            "sequence",
            name="uq_llm_messages_session_sequence",
        ),
    )
    op.create_index("ix_llm_messages_session_id", "llm_messages", ["session_id"])
    op.create_index("ix_llm_messages_run_id", "llm_messages", ["run_id"])
    op.create_index(
        "ix_llm_messages_parent_message_id",
        "llm_messages",
        ["parent_message_id"],
    )


def downgrade() -> None:
    """Remove immutable messages before their owning conversation sessions."""

    op.drop_table("llm_messages")
    op.drop_table("llm_sessions")
