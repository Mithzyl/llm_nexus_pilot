"""Create final-user login identities, password verifiers, sessions, and audit facts.

Revision ID: 20260905_0020
Revises: 20260831_0019
Create Date: 2026-09-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260905_0020"
down_revision: str | None = "20260831_0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _mutable_timestamp_columns() -> list[sa.Column]:
    """Return the standard creation and update columns for mutable identity records."""

    return [
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    ]


def upgrade() -> None:
    """Create authentication tables in the User owner's existing transactional database."""

    op.create_table(
        "user_auth_identities",
        sa.Column("auth_identity_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=128), nullable=False),
        sa.Column(
            "identity_provider",
            sa.String(length=32),
            nullable=False,
            server_default="local_password",
        ),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("normalized_email", sa.String(length=320), nullable=False),
        sa.Column("is_verified", sa.Boolean(), nullable=False, server_default=sa.true()),
        *_mutable_timestamp_columns(),
        sa.ForeignKeyConstraint(["user_id"], ["users.user_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("auth_identity_id"),
        sa.UniqueConstraint(
            "identity_provider",
            "normalized_email",
            name="uq_user_auth_identity_provider_email",
        ),
    )
    op.create_index(
        "ix_user_auth_identities_user_id",
        "user_auth_identities",
        ["user_id"],
    )
    op.create_index(
        "ix_user_auth_identity_user_provider",
        "user_auth_identities",
        ["user_id", "identity_provider"],
    )

    op.create_table(
        "user_password_credentials",
        sa.Column("auth_identity_id", sa.String(length=36), nullable=False),
        sa.Column("password_hash", sa.String(length=512), nullable=False),
        sa.Column(
            "password_hash_algorithm",
            sa.String(length=32),
            nullable=False,
            server_default="argon2id",
        ),
        sa.Column(
            "password_hash_version",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
        sa.Column(
            "password_changed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["auth_identity_id"],
            ["user_auth_identities.auth_identity_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("auth_identity_id"),
    )

    op.create_table(
        "user_login_sessions",
        sa.Column("login_session_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=128), nullable=False),
        sa.Column("auth_identity_id", sa.String(length=36), nullable=False),
        sa.Column("session_token_hash", sa.String(length=64), nullable=False),
        sa.Column("csrf_token_hash", sa.String(length=64), nullable=False),
        sa.Column("device_name", sa.String(length=128), nullable=True),
        sa.Column(
            "authenticated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "last_used_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("idle_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("absolute_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_reason", sa.String(length=64), nullable=True),
        *_mutable_timestamp_columns(),
        sa.ForeignKeyConstraint(
            ["auth_identity_id"],
            ["user_auth_identities.auth_identity_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.user_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("login_session_id"),
        sa.UniqueConstraint("session_token_hash", name="uq_user_login_session_token_hash"),
    )
    op.create_index(
        "ix_user_login_sessions_auth_identity_id",
        "user_login_sessions",
        ["auth_identity_id"],
    )
    op.create_index(
        "ix_user_login_sessions_user_id",
        "user_login_sessions",
        ["user_id"],
    )
    op.create_index(
        "ix_user_login_session_user_revoked_expires",
        "user_login_sessions",
        ["user_id", "revoked_at", "absolute_expires_at"],
    )

    op.create_table(
        "authentication_audit_events",
        sa.Column("authentication_audit_event_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=128), nullable=True),
        sa.Column("auth_identity_id", sa.String(length=36), nullable=True),
        sa.Column("login_session_id", sa.String(length=36), nullable=True),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("identifier_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("client_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["auth_identity_id"],
            ["user_auth_identities.auth_identity_id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["login_session_id"],
            ["user_login_sessions.login_session_id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.user_id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("authentication_audit_event_id"),
    )
    op.create_index(
        "ix_authentication_audit_events_auth_identity_id",
        "authentication_audit_events",
        ["auth_identity_id"],
    )
    op.create_index(
        "ix_authentication_audit_events_login_session_id",
        "authentication_audit_events",
        ["login_session_id"],
    )
    op.create_index(
        "ix_auth_audit_rate_limit",
        "authentication_audit_events",
        ["event_type", "identifier_fingerprint", "client_fingerprint", "occurred_at"],
    )
    op.create_index(
        "ix_authentication_audit_events_user_id",
        "authentication_audit_events",
        ["user_id"],
    )
    op.create_index(
        "ix_auth_audit_user_occurred",
        "authentication_audit_events",
        ["user_id", "occurred_at"],
    )


def downgrade() -> None:
    """Remove authentication facts without changing existing User-owned LLM resources."""

    op.drop_index("ix_auth_audit_user_occurred", table_name="authentication_audit_events")
    op.drop_index(
        "ix_authentication_audit_events_user_id",
        table_name="authentication_audit_events",
    )
    op.drop_index("ix_auth_audit_rate_limit", table_name="authentication_audit_events")
    op.drop_index(
        "ix_authentication_audit_events_login_session_id",
        table_name="authentication_audit_events",
    )
    op.drop_index(
        "ix_authentication_audit_events_auth_identity_id",
        table_name="authentication_audit_events",
    )
    op.drop_table("authentication_audit_events")
    op.drop_index(
        "ix_user_login_session_user_revoked_expires",
        table_name="user_login_sessions",
    )
    op.drop_index("ix_user_login_sessions_user_id", table_name="user_login_sessions")
    op.drop_index("ix_user_login_sessions_auth_identity_id", table_name="user_login_sessions")
    op.drop_table("user_login_sessions")
    op.drop_table("user_password_credentials")
    op.drop_index(
        "ix_user_auth_identity_user_provider",
        table_name="user_auth_identities",
    )
    op.drop_index("ix_user_auth_identities_user_id", table_name="user_auth_identities")
    op.drop_table("user_auth_identities")
