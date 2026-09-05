"""Focused migration checks for final-user authentication persistence."""

import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext


def test_user_authentication_migration_keeps_secrets_out_of_persistence() -> None:
    """Create all authentication tables and verify only secret digests are stored."""

    engine = sa.create_engine("sqlite:///:memory:")

    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "CREATE TABLE users (user_id VARCHAR(128) NOT NULL PRIMARY KEY)"
            )
        )
        migration_context = MigrationContext.configure(connection)
        migration_path = (
            Path(__file__).parents[1]
            / "migrations"
            / "versions"
            / "20260905_0020_user_authentication.py"
        )
        specification = importlib.util.spec_from_file_location(
            "user_authentication_migration",
            migration_path,
        )
        assert specification is not None and specification.loader is not None
        migration_module = importlib.util.module_from_spec(specification)
        specification.loader.exec_module(migration_module)
        with Operations.context(migration_context):
            migration_module.upgrade()

        inspector = sa.inspect(connection)
        assert {
            "user_auth_identities",
            "user_password_credentials",
            "user_login_sessions",
            "authentication_audit_events",
        } <= set(inspector.get_table_names())
        password_columns = {
            column["name"]
            for column in inspector.get_columns("user_password_credentials")
        }
        login_session_columns = {
            column["name"]
            for column in inspector.get_columns("user_login_sessions")
        }
        assert "password_hash" in password_columns
        assert "password" not in password_columns
        assert {"session_token_hash", "csrf_token_hash"} <= login_session_columns
        assert {"session_token", "csrf_token"}.isdisjoint(login_session_columns)

        with Operations.context(migration_context):
            migration_module.downgrade()

        assert set(sa.inspect(connection).get_table_names()) == {"users"}
