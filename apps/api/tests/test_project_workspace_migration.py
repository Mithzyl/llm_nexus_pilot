"""Exercise the additive project copy migration independently from live databases."""

import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def test_project_file_migration_preserves_existing_projects():
    """Upgrade/downgrade leave project identities untouched and add immutable copy constraints."""
    path = (
        Path(__file__).parents[1] / "migrations/versions/20261024_0029_project_workspace_files.py"
    )
    spec = importlib.util.spec_from_file_location("project_copy_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(
            sa.text("CREATE TABLE llm_projects (project_id VARCHAR(36) PRIMARY KEY)")
        )
        connection.execute(sa.text("INSERT INTO llm_projects VALUES ('existing')"))
        with Operations.context(MigrationContext.configure(connection)):
            module.upgrade()
        constraints = sa.inspect(connection).get_unique_constraints("project_workspace_files")
        assert {c["name"] for c in constraints} == {
            "uq_project_file_name",
            "uq_project_file_request",
        }
        with Operations.context(MigrationContext.configure(connection)):
            module.downgrade()
        assert connection.scalar(sa.text("SELECT project_id FROM llm_projects")) == "existing"
    engine.dispose()
