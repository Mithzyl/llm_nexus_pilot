"""Persist Provider endpoint addresses separately from deployment credentials.

Revision ID: 20260831_0019
Revises: 20260829_0018
Create Date: 2026-08-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260831_0019"
down_revision: str | None = "20260829_0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SUPPORTED_PROVIDER_NAMES = (
    "anthropic",
    "deepseek",
    "gemini",
    "openai",
    "openai_compatible",
)
DEEPSEEK_ENDPOINT_ID = "00000000-0000-0000-0000-000000000019"


def upgrade() -> None:
    """Create endpoint configuration, enforce one enabled address, and seed DeepSeek."""

    provider_endpoints = op.create_table(
        "llm_provider_endpoints",
        sa.Column("provider_endpoint_id", sa.String(length=36), nullable=False),
        sa.Column(
            "provider",
            sa.Enum(
                *SUPPORTED_PROVIDER_NAMES,
                name="providerendpointname",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("base_url", sa.String(length=2048), nullable=False),
        sa.Column(
            "enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
        sa.Column(
            "enabled_provider",
            sa.String(length=32),
            sa.Computed(
                "CASE WHEN enabled = 1 THEN provider ELSE NULL END",
                persisted=True,
            ),
            nullable=True,
        ),
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
        sa.PrimaryKeyConstraint("provider_endpoint_id"),
        sa.UniqueConstraint(
            "enabled_provider",
            name="uq_llm_provider_endpoint_enabled_provider",
        ),
    )
    op.create_index(
        "ix_llm_provider_endpoint_provider_enabled",
        "llm_provider_endpoints",
        ["provider", "enabled"],
        unique=False,
    )
    op.bulk_insert(
        provider_endpoints,
        [
            {
                "provider_endpoint_id": DEEPSEEK_ENDPOINT_ID,
                "provider": "deepseek",
                "base_url": "https://api.deepseek.com",
                "enabled": True,
            }
        ],
    )


def downgrade() -> None:
    """Remove Provider endpoint addresses without changing models or capabilities."""

    op.drop_index(
        "ix_llm_provider_endpoint_provider_enabled",
        table_name="llm_provider_endpoints",
    )
    op.drop_table("llm_provider_endpoints")
