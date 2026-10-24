"""Keep owner-scoped session creation identities across lost HTTP responses.

Apply through the existing Alembic workflow. Existing sessions remain unkeyed;
no private message body is stored by this migration.
"""

import sqlalchemy as sa
from alembic import op

revision = "20261024_0027"
down_revision = "20261023_0026"
branch_labels = None
depends_on = None


def upgrade():
    """Add nullable creation fingerprints without changing existing sessions."""
    with op.batch_alter_table("llm_sessions") as table:
        table.add_column(sa.Column("creation_key_hash", sa.String(64), nullable=True))
        table.add_column(sa.Column("creation_request_hash", sa.String(64), nullable=True))
        table.create_unique_constraint("uq_session_owner_creation_key",
                                       ["user_id", "creation_key_hash"])


def downgrade():
    """Remove recovery identities only on an explicitly requested migration rollback."""
    with op.batch_alter_table("llm_sessions") as table:
        table.drop_constraint("uq_session_owner_creation_key", type_="unique")
        table.drop_column("creation_request_hash")
        table.drop_column("creation_key_hash")
