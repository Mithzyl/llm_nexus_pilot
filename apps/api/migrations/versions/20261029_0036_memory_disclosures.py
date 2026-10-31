"""Preserve existing single-Run consent while allowing independent Run disclosure records.

Run the standard Alembic upgrade. No memory ciphertext or owner is changed.
Downgrade refuses to discard newly recorded consent rather than choosing a winning Run.
"""

import uuid

import sqlalchemy as sa
from alembic import op

revision = "20261029_0036"
down_revision = "20261029_0035"
branch_labels = None
depends_on = None


def upgrade():
    """Copy only existing active grants to their original owner, Run and source entry."""
    table = op.create_table(
        "user_memory_disclosures",
        sa.Column("disclosure_id", sa.String(36), primary_key=True),
        sa.Column(
            "entry_id", sa.String(36), sa.ForeignKey("user_memory_entries.entry_id"), nullable=False
        ),
        sa.Column("entry_version", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("llm_runs.run_id"), nullable=False),
        sa.Column("recipient_entry", sa.String(64), nullable=False),
        sa.Column("allowed_fields_json", sa.JSON(), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("request_key", sa.String(128), nullable=False),
        sa.UniqueConstraint(
            "entry_id",
            "entry_version",
            "run_id",
            "recipient_entry",
            name="uq_memory_disclosure_recipient",
        ),
    )
    op.create_index("ix_user_memory_disclosures_run_id", "user_memory_disclosures", ["run_id"])
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT e.entry_id,e.version,e.disclosure_run_id,s.entry,e.updated_at "
            "FROM user_memory_entries e JOIN user_memory_scopes s ON s.scope_id=e.scope_id "
            "JOIN llm_runs r ON r.run_id=e.disclosure_run_id "
            "WHERE e.disclosure_run_id IS NOT NULL AND e.status='active' "
            "AND r.user_id=s.owner_user_id"
        )
    ).mappings()
    for row in rows:
        bind.execute(
            table.insert().values(
                disclosure_id=str(uuid.uuid4()),
                entry_id=row["entry_id"],
                entry_version=row["version"],
                run_id=row["disclosure_run_id"],
                recipient_entry=row["entry"],
                allowed_fields_json=["text", "app_data"],
                confirmed_at=row["updated_at"],
                request_key="legacy-disclosure:" + row["entry_id"],
            )
        )


def downgrade():
    """Stop if consent changed after upgrade; never silently erase multi-Run grants."""
    bind = op.get_bind()
    if bind.execute(
        sa.text(
            "SELECT disclosure_id FROM user_memory_disclosures "
            "WHERE request_key NOT LIKE 'legacy-disclosure:%' LIMIT 1"
        )
    ).first():
        raise RuntimeError("Memory disclosure data requires explicit export before downgrade")
    op.drop_table("user_memory_disclosures")
