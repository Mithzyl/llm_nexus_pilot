"""Check the personal credential migration without touching runtime users or credentials."""

import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext


def test_personal_key_migration_preserves_existing_identity_and_runs():
    """Add an empty credential table and leave historical owner/session/run rows unchanged."""
    path = Path(__file__).parents[1] / "migrations/versions/20261023_0026_personal_api_keys.py"
    spec = importlib.util.spec_from_file_location("personal_key_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with sa.create_engine("sqlite:///:memory:").begin() as connection:
        for table, column in [
            ("users", "user_id"),
            ("llm_sessions", "session_id"),
            ("llm_runs", "run_id"),
        ]:
            connection.exec_driver_sql(f"CREATE TABLE {table} ({column} VARCHAR(128) PRIMARY KEY)")
            connection.exec_driver_sql(f"INSERT INTO {table} VALUES ('existing')")
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
        assert connection.exec_driver_sql("SELECT COUNT(*) FROM user_api_keys").scalar() == 0
        for table in ("users", "llm_sessions", "llm_runs"):
            assert connection.exec_driver_sql(f"SELECT * FROM {table}").scalar() == "existing"
        with Operations.context(MigrationContext.configure(connection)):
            migration.downgrade()
        assert connection.exec_driver_sql("SELECT user_id FROM users").scalar() == "existing"
