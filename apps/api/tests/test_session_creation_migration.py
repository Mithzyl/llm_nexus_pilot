"""Verify the additive session-recovery migration without altering a development database."""

import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext


def test_session_identity_migration_preserves_unkeyed_sessions():
    """Existing sessions survive upgrade/rollback; duplicate owner/key pairs are rejected."""
    path = Path(__file__).parents[1] / (
        "migrations/versions/20261024_0027_session_creation_identity.py")
    spec = importlib.util.spec_from_file_location("session_identity_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(sa.text(
            "CREATE TABLE llm_sessions (session_id VARCHAR(36) PRIMARY KEY, user_id VARCHAR(128))"))
        connection.execute(sa.text("INSERT INTO llm_sessions VALUES ('existing','owner')"))
        with Operations.context(MigrationContext.configure(connection)):
            module.upgrade()
        assert connection.execute(sa.text(
            "SELECT session_id,creation_key_hash FROM llm_sessions")).one() == ("existing", None)
        connection.execute(sa.text(
            "INSERT INTO llm_sessions (session_id,user_id,creation_key_hash) "
            "VALUES ('keyed','owner','key')"))
        import pytest
        with pytest.raises(sa.exc.IntegrityError), connection.begin_nested():
            connection.execute(sa.text(
                "INSERT INTO llm_sessions (session_id,user_id,creation_key_hash) "
                "VALUES ('duplicate','owner','key')"))
        with Operations.context(MigrationContext.configure(connection)):
            module.downgrade()
        assert connection.scalar(sa.text("SELECT count(*) FROM llm_sessions")) == 2
    engine.dispose()


def test_workflow_ownership_migration_keeps_existing_execution_inline():
    """Adding leases never claims historical work; rollback preserves its stored outcome."""
    path = Path(__file__).parents[1] / (
        "migrations/versions/20261024_0028_workflow_execution_ownership.py")
    spec = importlib.util.spec_from_file_location("workflow_ownership_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(sa.text(
            "CREATE TABLE llm_agent_workflow_executions "
            "(workflow_execution_id VARCHAR(36) PRIMARY KEY, status VARCHAR(32))"))
        connection.execute(sa.text(
            "INSERT INTO llm_agent_workflow_executions VALUES ('existing','COMPLETED')"))
        with Operations.context(MigrationContext.configure(connection)):
            module.upgrade()
        assert connection.execute(sa.text(
            "SELECT status, execution_backend, execution_owner, execution_lease_version, "
            "execution_lease_expires_at FROM llm_agent_workflow_executions"
        )).one() == ("COMPLETED", "inline", None, 0, None)
        with Operations.context(MigrationContext.configure(connection)):
            module.downgrade()
        assert connection.execute(sa.text(
            "SELECT * FROM llm_agent_workflow_executions"
        )).one() == ("existing", "COMPLETED")
    engine.dispose()
