"""Focused migration checks for database-managed Provider endpoint addresses."""

import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy.exc import IntegrityError


def test_provider_endpoint_migration_seeds_deepseek_and_limits_enabled_address() -> None:
    """Create the endpoint table, seed DeepSeek, and reject a second enabled address."""

    engine = sa.create_engine("sqlite:///:memory:")

    with engine.begin() as connection:
        migration_context = MigrationContext.configure(connection)
        migration_path = (
            Path(__file__).parents[1]
            / "migrations"
            / "versions"
            / "20260831_0019_provider_endpoints.py"
        )
        specification = importlib.util.spec_from_file_location(
            "provider_endpoint_migration",
            migration_path,
        )
        assert specification is not None and specification.loader is not None
        migration_module = importlib.util.module_from_spec(specification)
        specification.loader.exec_module(migration_module)
        with Operations.context(migration_context):
            migration_module.upgrade()

        columns = {
            column["name"]
            for column in sa.inspect(connection).get_columns("llm_provider_endpoints")
        }
        seeded_row = (
            connection.execute(
                sa.text(
                    "SELECT provider, base_url, enabled FROM llm_provider_endpoints "
                    "WHERE provider = 'deepseek'"
                )
            )
            .mappings()
            .one()
        )
        connection.execute(
            sa.text(
                "INSERT INTO llm_provider_endpoints "
                "(provider_endpoint_id, provider, base_url, enabled) "
                "VALUES ('disabled-deepseek-endpoint', 'deepseek', "
                "'https://disabled.example.com/v1', 0)"
            )
        )

        with pytest.raises(IntegrityError):
            with connection.begin_nested():
                connection.execute(
                    sa.text(
                        "INSERT INTO llm_provider_endpoints "
                        "(provider_endpoint_id, provider, base_url, enabled) "
                        "VALUES ('second-enabled-deepseek', 'deepseek', "
                        "'https://second.example.com/v1', 1)"
                    )
                )

    assert {
        "provider_endpoint_id",
        "provider",
        "base_url",
        "enabled",
        "created_at",
        "updated_at",
    } <= columns
    assert {"api_key", "secret", "credential"}.isdisjoint(columns)
    assert seeded_row == {
        "provider": "deepseek",
        "base_url": "https://api.deepseek.com",
        "enabled": 1,
    }
