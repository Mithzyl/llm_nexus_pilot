"""Add explicit account links; never rewrite existing users or resource owners."""

import sqlalchemy as sa
from alembic import op

revision = "20261030_0042"
down_revision = "20261029_0041"
branch_labels = None
depends_on = None


def upgrade():
    """Save short-lived two-party requests and revocable links without business content."""
    op.create_table(
        "account_link_requests",
        sa.Column("request_id", sa.String(36), primary_key=True),
        sa.Column(
            "mapping_id",
            sa.String(36),
            sa.ForeignKey("application_user_mappings.mapping_id"),
            nullable=False,
        ),
        sa.Column("ticket_digest", sa.String(64), unique=True, nullable=False),
        sa.Column("app_verified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "ecosystem_account_links",
        sa.Column("link_id", sa.String(36), primary_key=True),
        sa.Column(
            "primary_principal_id",
            sa.String(128),
            sa.ForeignKey("users.user_id"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "linked_principal_id",
            sa.String(128),
            sa.ForeignKey("users.user_id"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "mapping_id",
            sa.String(36),
            sa.ForeignKey("application_user_mappings.mapping_id"),
            unique=True,
            nullable=False,
        ),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("app_verified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("browser_verified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("version", sa.Integer(), nullable=False),
    )


def downgrade():
    """Require an explicit export before removing any saved consent records."""
    for table in ("ecosystem_account_links", "account_link_requests"):
        if op.get_bind().execute(sa.text(f"SELECT 1 FROM {table} LIMIT 1")).first():
            raise RuntimeError("Export account link decisions before downgrade")
    op.drop_table("ecosystem_account_links")
    op.drop_table("account_link_requests")
