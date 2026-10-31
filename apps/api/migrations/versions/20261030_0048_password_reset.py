"""Add purpose-specific password reset proofs without changing accounts or credentials."""

import sqlalchemy as sa
from alembic import op

revision = "20261030_0048"
down_revision = "20261030_0047"
branch_labels = None
depends_on = None


def upgrade():
    """Keep reset proof separate from registration, bound to its requested credential version."""
    op.create_table(
        "password_reset_challenges",
        sa.Column("normalized_email", sa.String(320), primary_key=True),
        sa.Column(
            "auth_identity_id",
            sa.String(36),
            sa.ForeignKey("user_auth_identities.auth_identity_id"),
        ),
        sa.Column("encrypted_code", sa.String(512)),
        sa.Column("credential_updated_at", sa.DateTime(timezone=True)),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("delivered", sa.Boolean(), nullable=False),
        sa.Column("failed_attempts", sa.Integer(), nullable=False),
    )


def downgrade():
    """Require explicit export before removing outstanding or consumed reset records."""
    if op.get_bind().execute(sa.text("SELECT 1 FROM password_reset_challenges LIMIT 1")).first():
        raise RuntimeError("Export reset challenges before downgrade")
    op.drop_table("password_reset_challenges")
