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


def test_app_principal_migration_preserves_owner_and_allows_second_app():
    """Upgrade a historical association without changing its owner or weakening App uniqueness."""
    root = Path(__file__).parents[1] / "migrations/versions"
    modules = []
    for filename in ("20261023_0025_application_tool_identity.py",
                     "20261029_0035_application_principal_scope.py"):
        spec = importlib.util.spec_from_file_location(filename[:-3], root / filename)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        modules.append(module)
    with sa.create_engine("sqlite:///:memory:").begin() as connection:
        connection.exec_driver_sql("CREATE TABLE users (user_id VARCHAR(128) PRIMARY KEY)")
        connection.exec_driver_sql("CREATE TABLE llm_runs (run_id VARCHAR(36) PRIMARY KEY)")
        with Operations.context(MigrationContext.configure(connection)):
            modules[0].upgrade()
            connection.exec_driver_sql("INSERT INTO users VALUES ('owner')")
            connection.exec_driver_sql("INSERT INTO applications(application_id,is_enabled) VALUES ('assistant',1),('retail',1)")
            connection.exec_driver_sql("INSERT INTO application_user_mappings(mapping_id,application_id,external_user_id,pilot_principal_id,is_enabled) VALUES ('old','assistant','subject','owner',1)")
            modules[1].upgrade()
            assert connection.exec_driver_sql("SELECT pilot_principal_id FROM application_user_mappings WHERE mapping_id='old'").scalar() == "owner"
            connection.exec_driver_sql("INSERT INTO application_user_mappings(mapping_id,application_id,external_user_id,pilot_principal_id,is_enabled) VALUES ('new','retail','subject','owner',1)")
            import pytest
            with pytest.raises(RuntimeError, match="multi-App"):
                modules[1].downgrade()
