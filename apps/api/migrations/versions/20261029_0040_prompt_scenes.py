"""Add approved scene bindings and per-Run references without changing historical prompts."""

import sqlalchemy as sa
from alembic import op

revision = "20261029_0040"
down_revision = "20261029_0039"
branch_labels = None
depends_on = None


def upgrade():
    """Create empty catalogs; default selection uses only a registered scene."""
    op.create_table(
        "prompt_scene_bindings",
        sa.Column("binding_id", sa.String(36), primary_key=True),
        sa.Column("entry", sa.String(64), nullable=False),
        sa.Column("scene_id", sa.String(64), nullable=False),
        sa.Column("execution_mode", sa.String(16), nullable=False),
        sa.Column(
            "template_version_id",
            sa.String(36),
            sa.ForeignKey("llm_prompt_template_versions.template_version_id"),
            nullable=False,
        ),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("entry", "scene_id", "execution_mode", name="uq_prompt_scene_mode"),
    )
    op.create_table(
        "run_prompt_scene_selections",
        sa.Column("run_id", sa.String(36), sa.ForeignKey("llm_runs.run_id"), primary_key=True),
        sa.Column("entry", sa.String(64), nullable=False),
        sa.Column("explicit_scene", sa.Boolean(), nullable=False),
        sa.Column("selections_json", sa.JSON(), nullable=False),
    )


def downgrade():
    """Require an export before dropping any published or execution-pinned scene records."""
    for name in ("run_prompt_scene_selections", "prompt_scene_bindings"):
        if (
            op.get_bind()
            .execute(sa.select(sa.literal(1)).select_from(sa.table(name)).limit(1))
            .first()
        ):
            raise RuntimeError("Export scene records before downgrade")
    op.drop_table("run_prompt_scene_selections")
    op.drop_table("prompt_scene_bindings")
