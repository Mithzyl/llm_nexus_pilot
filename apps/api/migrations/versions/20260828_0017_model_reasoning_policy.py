"""Persist stable model reasoning policies and invocation evidence.

Revision ID: 20260828_0017
Revises: 20260827_0016
Create Date: 2026-08-28
"""

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260828_0017"
down_revision: str | None = "20260827_0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DEFAULT_PROFILE_VERSION = "reasoning.v1"
DEEPSEEK_EFFORTS = ["minimal", "low", "medium", "high", "xhigh", "max"]
DEEPSEEK_EFFORT_MAPPING = {
    "minimal": "low",
    "low": "low",
    "medium": "high",
    "high": "high",
    "xhigh": "high",
    "max": "max",
}


def upgrade() -> None:
    """Add model-level reasoning policy and immutable per-attempt resolution evidence."""

    op.add_column(
        "llm_models",
        sa.Column("reasoning_supported_efforts_json", sa.JSON(), nullable=True),
    )
    op.add_column(
        "llm_models",
        sa.Column("reasoning_supports_disable", sa.Boolean(), nullable=True),
    )
    op.add_column(
        "llm_models",
        sa.Column("reasoning_default_enabled", sa.Boolean(), nullable=True),
    )
    op.add_column(
        "llm_models",
        sa.Column("reasoning_default_effort", sa.String(length=16), nullable=True),
    )
    op.add_column(
        "llm_models",
        sa.Column("reasoning_effort_mapping_json", sa.JSON(), nullable=True),
    )
    op.add_column(
        "llm_models",
        sa.Column("reasoning_profile_version", sa.String(length=64), nullable=True),
    )

    op.execute(
        sa.text(
            "UPDATE llm_models SET "
            "reasoning_supported_efforts_json = '[]', "
            "reasoning_supports_disable = 0, "
            "reasoning_default_enabled = 1, "
            "reasoning_default_effort = 'medium', "
            "reasoning_effort_mapping_json = '{}', "
            f"reasoning_profile_version = '{DEFAULT_PROFILE_VERSION}'"
        )
    )
    deepseek_efforts_json = json.dumps(DEEPSEEK_EFFORTS, separators=(",", ":"))
    deepseek_mapping_json = json.dumps(
        DEEPSEEK_EFFORT_MAPPING,
        separators=(",", ":"),
    )
    op.execute(
        sa.text(
            "UPDATE llm_models SET "
            f"reasoning_supported_efforts_json = '{deepseek_efforts_json}', "
            "reasoning_supports_disable = 1, "
            f"reasoning_effort_mapping_json = '{deepseek_mapping_json}' "
            "WHERE provider = 'deepseek'"
        )
    )
    with op.batch_alter_table("llm_models") as batch_op:
        batch_op.alter_column(
            "reasoning_supported_efforts_json",
            existing_type=sa.JSON(),
            nullable=False,
        )
        batch_op.alter_column(
            "reasoning_supports_disable",
            existing_type=sa.Boolean(),
            nullable=False,
        )
        batch_op.alter_column(
            "reasoning_default_enabled",
            existing_type=sa.Boolean(),
            nullable=False,
        )
        batch_op.alter_column(
            "reasoning_default_effort",
            existing_type=sa.String(length=16),
            nullable=False,
        )
        batch_op.alter_column(
            "reasoning_effort_mapping_json",
            existing_type=sa.JSON(),
            nullable=False,
        )
        batch_op.alter_column(
            "reasoning_profile_version",
            existing_type=sa.String(length=64),
            nullable=False,
        )

    op.add_column(
        "llm_attempts",
        sa.Column("reasoning_enabled", sa.Boolean(), nullable=True),
    )
    op.add_column(
        "llm_attempts",
        sa.Column("reasoning_effort", sa.String(length=16), nullable=True),
    )
    op.add_column(
        "llm_attempts",
        sa.Column("reasoning_source", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "llm_attempts",
        sa.Column("reasoning_profile_version", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "llm_attempts",
        sa.Column("reasoning_provider_options_json", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    """Remove reasoning policy and resolution evidence without changing prior attempts."""

    op.drop_column("llm_attempts", "reasoning_provider_options_json")
    op.drop_column("llm_attempts", "reasoning_profile_version")
    op.drop_column("llm_attempts", "reasoning_source")
    op.drop_column("llm_attempts", "reasoning_effort")
    op.drop_column("llm_attempts", "reasoning_enabled")
    op.drop_column("llm_models", "reasoning_profile_version")
    op.drop_column("llm_models", "reasoning_effort_mapping_json")
    op.drop_column("llm_models", "reasoning_default_effort")
    op.drop_column("llm_models", "reasoning_default_enabled")
    op.drop_column("llm_models", "reasoning_supports_disable")
    op.drop_column("llm_models", "reasoning_supported_efforts_json")
