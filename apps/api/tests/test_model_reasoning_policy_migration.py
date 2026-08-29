"""Focused migration checks for model reasoning policy and attempt evidence."""

import importlib.util
import json
from pathlib import Path

import sqlalchemy as sa
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext


def test_model_reasoning_policy_migration_backfills_existing_deepseek_model() -> None:
    """Verify existing DeepSeek rows gain a valid medium default and explicit mapping."""

    engine = sa.create_engine("sqlite:///:memory:")
    metadata = sa.MetaData()
    registered_models = sa.Table(
        "llm_models",
        metadata,
        sa.Column("model_id", sa.String(36), primary_key=True),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("model_identifier", sa.String(128), nullable=False),
    )
    sa.Table(
        "llm_attempts",
        metadata,
        sa.Column("attempt_id", sa.String(36), primary_key=True),
    )
    metadata.create_all(engine)

    with engine.begin() as connection:
        connection.execute(
            registered_models.insert(),
            {
                "model_id": "deepseek-model-id",
                "provider": "deepseek",
                "model_identifier": "deepseek-v4-flash",
            },
        )
        migration_context = MigrationContext.configure(connection)
        migration_path = (
            Path(__file__).parents[1]
            / "migrations"
            / "versions"
            / "20260828_0017_model_reasoning_policy.py"
        )
        specification = importlib.util.spec_from_file_location(
            "model_reasoning_policy_migration",
            migration_path,
        )
        assert specification is not None and specification.loader is not None
        migration_module = importlib.util.module_from_spec(specification)
        specification.loader.exec_module(migration_module)
        with Operations.context(migration_context):
            migration_module.upgrade()

        row = (
            connection.execute(
                sa.text(
                    "SELECT reasoning_default_enabled, reasoning_default_effort, "
                    "reasoning_supported_efforts_json, reasoning_effort_mapping_json, "
                    "reasoning_profile_version FROM llm_models"
                )
            )
            .mappings()
            .one()
        )
        attempt_columns = {
            column["name"] for column in sa.inspect(connection).get_columns("llm_attempts")
        }

    assert row["reasoning_default_enabled"] == 1
    assert row["reasoning_default_effort"] == "medium"
    assert "medium" in json.loads(row["reasoning_supported_efforts_json"])
    assert json.loads(row["reasoning_effort_mapping_json"])["medium"] == "high"
    assert row["reasoning_profile_version"] == "reasoning.v1"
    assert {
        "reasoning_enabled",
        "reasoning_effort",
        "reasoning_source",
        "reasoning_profile_version",
        "reasoning_provider_options_json",
    } <= attempt_columns
