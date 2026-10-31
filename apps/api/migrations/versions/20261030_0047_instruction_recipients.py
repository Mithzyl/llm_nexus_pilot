"""Bind new global instruction consent to exact recipients without expanding old approvals."""

import sqlalchemy as sa
from alembic import op

revision = "20261030_0047"
down_revision = "20261030_0046"
branch_labels = None
depends_on = None


def upgrade():
    """Empty legacy recipient maps authorize no new cross-owner sharing."""
    op.add_column(
        "user_instruction_profiles", sa.Column("allowed_recipients_json", sa.JSON(), nullable=True)
    )
    op.execute(sa.text("UPDATE user_instruction_profiles SET allowed_recipients_json = '{}'"))
    with op.batch_alter_table("user_instruction_profiles") as batch:
        batch.alter_column("allowed_recipients_json", existing_type=sa.JSON(), nullable=False)


def downgrade():
    """Require export rather than silently broadening recipient-specific consent."""
    if op.get_bind().execute(sa.text("SELECT 1 FROM user_instruction_profiles LIMIT 1")).first():
        raise RuntimeError("Export instruction recipient approvals before downgrade")
    op.drop_column("user_instruction_profiles", "allowed_recipients_json")
