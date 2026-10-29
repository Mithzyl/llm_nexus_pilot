"""Add pending email challenges; existing users and verified identities remain unchanged.

Run with the existing Alembic upgrade command. Downgrade drops only pending
verification records; it does not delete accounts created after verification.
"""

import sqlalchemy as sa
from alembic import op

revision = "20261029_0031"
down_revision = "20261027_0030"
branch_labels = None
depends_on = None


def upgrade():
    """Create encrypted verification storage without activating registration."""
    op.create_table(
        "registration_challenges",
        sa.Column("normalized_email", sa.String(320), primary_key=True),
        sa.Column("encrypted_code", sa.String(512), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("delivered", sa.Boolean(), nullable=False),
        sa.Column("failed_attempts", sa.Integer(), nullable=False),
    )


def downgrade():
    """Remove pending challenges only when an operator explicitly downgrades."""
    op.drop_table("registration_challenges")
