"""Scope principal uniqueness to each App; upgrade with the existing Alembic command.

No owners, mappings, login identities or private data are merged or reassigned.
Downgrade refuses to collapse valid multi-App associations.
"""

import sqlalchemy as sa
from alembic import op

revision = "20261029_0035"
down_revision = "20261029_0034"
branch_labels = None
depends_on = None

TABLE = "application_user_mappings"
INDEX = "ix_application_user_mappings_pilot_principal_id"


def upgrade():
    """Keep the foreign-key index while replacing global uniqueness with App/owner uniqueness."""
    bind = op.get_bind()
    constraints = sa.inspect(bind).get_unique_constraints(TABLE)
    old = next(c for c in constraints if c["column_names"] == ["pilot_principal_id"])
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table(
            TABLE, naming_convention={"uq": "uq_%(table_name)s_%(column_0_name)s"}
        ) as batch:
            batch.drop_constraint(
                old["name"] or "uq_application_user_mappings_pilot_principal_id", type_="unique"
            )
            batch.create_unique_constraint(
                "uq_application_principal", ["application_id", "pilot_principal_id"]
            )
            batch.create_index(INDEX, ["pilot_principal_id"])
    else:
        # MySQL cannot drop the unique index while it is the only FK-supporting index.
        op.create_index(INDEX, TABLE, ["pilot_principal_id"])
        op.create_unique_constraint(
            "uq_application_principal", TABLE, ["application_id", "pilot_principal_id"]
        )
        op.drop_constraint(old["name"], TABLE, type_="unique")


def downgrade():
    """Reject lossy rollback; restore global uniqueness only when existing rows allow it."""
    bind = op.get_bind()
    duplicate = bind.execute(
        sa.text(
            "SELECT pilot_principal_id FROM application_user_mappings "
            "GROUP BY pilot_principal_id HAVING COUNT(*) > 1 LIMIT 1"
        )
    ).first()
    if duplicate:
        raise RuntimeError("Cannot restore global uniqueness while multi-App mappings exist")
    with op.batch_alter_table(TABLE) as batch:
        batch.create_unique_constraint(
            "uq_application_user_mappings_pilot_principal_id", ["pilot_principal_id"]
        )
        batch.drop_constraint("uq_application_principal", type_="unique")
        batch.drop_index(INDEX)
