"""Add one-use login grants; no users, application associations or sessions are rewritten."""

import sqlalchemy as sa
from alembic import op

revision = "20261029_0041"
down_revision = "20261029_0040"
branch_labels = None
depends_on = None


def upgrade():
    """Store bounded standard authentication verifiers, never raw login codes or passwords."""
    op.create_table(
        "application_login_grants",
        sa.Column("grant_id", sa.String(36), primary_key=True),
        sa.Column(
            "application_id",
            sa.String(64),
            sa.ForeignKey("applications.application_id"),
            nullable=False,
        ),
        sa.Column("user_id", sa.String(128), sa.ForeignKey("users.user_id"), nullable=False),
        sa.Column(
            "login_session_id",
            sa.String(36),
            sa.ForeignKey("user_login_sessions.login_session_id"),
            nullable=False,
        ),
        sa.Column("code_digest", sa.String(64), unique=True, nullable=False),
        sa.Column("redirect_uri", sa.String(2048), nullable=False),
        sa.Column("code_challenge", sa.String(43), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True)),
        sa.Column("registration_ticket_digest", sa.String(64), unique=True),
        sa.Column("registration_completed", sa.Boolean(), nullable=False),
    )


def downgrade():
    """Require explicit export before dropping existing authorization decisions."""
    if (
        op.get_bind()
        .execute(sa.text("SELECT grant_id FROM application_login_grants LIMIT 1"))
        .first()
    ):
        raise RuntimeError("Export login grants before downgrade")
    op.drop_table("application_login_grants")
