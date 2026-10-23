"""Exercise the additive application migration on an isolated database, not local user data."""

import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext


def test_application_migration_preserves_native_runs():
    """Create and remove application tables while existing native rows remain unchanged."""
    path = (Path(__file__).parents[1]
            / "migrations/versions/20261023_0025_application_tool_identity.py")
    spec = importlib.util.spec_from_file_location("application_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with sa.create_engine("sqlite:///:memory:").begin() as connection:
        connection.exec_driver_sql("CREATE TABLE users (user_id VARCHAR(128) PRIMARY KEY)")
        connection.exec_driver_sql("CREATE TABLE llm_runs (run_id VARCHAR(36) PRIMARY KEY)")
        connection.exec_driver_sql("INSERT INTO llm_runs VALUES ('old-run')")
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
        assert connection.exec_driver_sql("SELECT COUNT(*) FROM applications").scalar() == 0
        count = connection.exec_driver_sql("SELECT COUNT(*) FROM run_application_context").scalar()
        assert count == 0
        with Operations.context(MigrationContext.configure(connection)):
            migration.downgrade()
        assert connection.exec_driver_sql("SELECT run_id FROM llm_runs").scalar() == "old-run"
