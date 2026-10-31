"""Add encrypted preference versions and Run selections; historical Runs remain empty."""

import sqlalchemy as sa
from alembic import op

revision = "20261029_0039"
down_revision = "20261029_0038"
branch_labels = None
depends_on = None


def timestamps():
    """Return fresh SQL timestamp columns matching current models."""
    return [
        sa.Column(name, sa.DateTime(timezone=True), server_default=sa.func.now())
        for name in ("created_at", "updated_at")
    ]


def upgrade():
    """Create empty stores without seeding preferences or cross-entry consent."""
    op.create_table(
        "user_instruction_profiles",
        sa.Column("profile_id", sa.String(36), primary_key=True),
        sa.Column("owner_user_id", sa.String(128), sa.ForeignKey("users.user_id"), nullable=False),
        sa.Column("scope_key", sa.String(64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("current_version", sa.Integer(), nullable=False),
        sa.Column("allowed_entries_json", sa.JSON(), nullable=False),
        *timestamps(),
        sa.UniqueConstraint("owner_user_id", "scope_key", name="uq_instruction_owner_scope"),
    )
    op.create_table(
        "user_instruction_versions",
        sa.Column(
            "profile_id",
            sa.String(36),
            sa.ForeignKey("user_instruction_profiles.profile_id"),
            primary_key=True,
        ),
        sa.Column("version", sa.Integer(), primary_key=True),
        sa.Column("body_uri", sa.String(1024), nullable=False),
        *timestamps(),
    )
    op.create_table(
        "run_instruction_selections",
        sa.Column("run_id", sa.String(36), sa.ForeignKey("llm_runs.run_id"), primary_key=True),
        sa.Column("entry", sa.String(64), nullable=False),
        sa.Column("references_json", sa.JSON(), nullable=False),
    )


def downgrade():
    """Do not discard populated preferences or execution selections without an explicit export."""
    for name in (
        "run_instruction_selections",
        "user_instruction_versions",
        "user_instruction_profiles",
    ):
        if (
            op.get_bind()
            .execute(sa.select(sa.literal(1)).select_from(sa.table(name)).limit(1))
            .first()
        ):
            raise RuntimeError("Export user instruction data before downgrade")
    for name in (
        "run_instruction_selections",
        "user_instruction_versions",
        "user_instruction_profiles",
    ):
        op.drop_table(name)
