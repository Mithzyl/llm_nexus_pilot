"""Focused migration checks for normalized model cache diagnostic facts."""

import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext


def test_model_cache_diagnostics_migration_adds_attempt_facts() -> None:
    """Verify the migration adds cache write, miss, and Agent role columns."""

    engine = sa.create_engine("sqlite:///:memory:")
    metadata = sa.MetaData()
    sa.Table(
        "llm_attempts",
        metadata,
        sa.Column("attempt_id", sa.String(36), primary_key=True),
    )
    metadata.create_all(engine)

    with engine.begin() as connection:
        migration_context = MigrationContext.configure(connection)
        migration_path = (
            Path(__file__).parents[1]
            / "migrations"
            / "versions"
            / "20260829_0018_model_cache_diagnostics.py"
        )
        specification = importlib.util.spec_from_file_location(
            "model_cache_diagnostics_migration",
            migration_path,
        )
        assert specification is not None and specification.loader is not None
        migration_module = importlib.util.module_from_spec(specification)
        specification.loader.exec_module(migration_module)
        with Operations.context(migration_context):
            migration_module.upgrade()

        columns = {
            column["name"] for column in sa.inspect(connection).get_columns("llm_attempts")
        }

    assert {"agent_role", "cache_write_tokens", "cache_miss_tokens"} <= columns
