"""Create stable registered models and backfill capability identities.

Revision ID: 20260827_0016
Revises: 20260827_0015
Create Date: 2026-08-27
"""

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision: str = "20260827_0016"
down_revision: str | None = "20260827_0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SUPPORTED_PROVIDER_NAMES = {
    "openai",
    "deepseek",
    "anthropic",
    "gemini",
    "openai_compatible",
}


def upgrade() -> None:
    """Create stable model identities and backfill supported historical catalog pairs."""

    registered_models = op.create_table(
        "llm_models",
        sa.Column("model_id", sa.String(length=36), nullable=False),
        sa.Column(
            "provider",
            sa.Enum(
                *sorted(SUPPORTED_PROVIDER_NAMES),
                name="providername",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("model_identifier", sa.String(length=128), nullable=False),
        sa.Column("display_name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "enabled",
                "disabled",
                name="modelregistrationstatus",
                native_enum=False,
                length=32,
            ),
            nullable=False,
            server_default="enabled",
        ),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("model_id"),
        sa.UniqueConstraint(
            "provider",
            "model_identifier",
            name="uq_llm_model_provider_identifier",
        ),
    )
    op.create_index(
        "ix_llm_model_directory_order",
        "llm_models",
        ["provider", "status", "sort_order", "display_name", "model_identifier"],
        unique=False,
    )
    op.create_index("ix_llm_models_status", "llm_models", ["status"], unique=False)
    _backfill_registered_models(registered_models)


def _backfill_registered_models(registered_models: sa.Table) -> None:
    """Deduplicate supported provider/model capability history into stable model rows."""

    catalog_versions = sa.table(
        "llm_model_catalog_versions",
        sa.column("provider", sa.String(length=64)),
        sa.column("model", sa.String(length=128)),
    )
    historical_pairs = op.get_bind().execute(
        sa.select(catalog_versions.c.provider, catalog_versions.c.model)
        .distinct()
        .order_by(catalog_versions.c.provider, catalog_versions.c.model)
    )
    now = datetime.now(UTC)
    rows = [
        {
            "model_id": str(uuid.uuid4()),
            "provider": provider,
            "model_identifier": model_identifier,
            "display_name": model_identifier,
            "description": None,
            "status": "enabled",
            "sort_order": sort_order,
            "metadata_json": {"registration_source": "catalog_migration"},
            "created_at": now,
            "updated_at": now,
        }
        for sort_order, (provider, model_identifier) in enumerate(
            historical_pairs,
            start=1,
        )
        if provider in SUPPORTED_PROVIDER_NAMES
    ]
    if rows:
        op.bulk_insert(registered_models, rows)


def downgrade() -> None:
    """Remove stable registrations while preserving immutable capability history."""

    op.drop_index("ix_llm_models_status", table_name="llm_models")
    op.drop_index("ix_llm_model_directory_order", table_name="llm_models")
    op.drop_table("llm_models")
