"""Exercise new call identity and nullable history on an isolated SQLite database."""

import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy.exc import IntegrityError


def test_tool_migration_preserves_history_and_enforces_attempt_call_identity():
    """Upgrade actual old columns, retain legacy rows, reject duplicates, and downgrade cleanly."""
    path = Path(__file__).parents[1] / "migrations/versions/20261008_0023_agent_file_tools.py"
    spec = importlib.util.spec_from_file_location("tool_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with sa.create_engine("sqlite:///:memory:").begin() as connection:
        connection.execute(
            sa.text(
                "CREATE TABLE llm_tool_calls (tool_call_id VARCHAR(36) PRIMARY KEY, "
                "attempt_id VARCHAR(36) NOT NULL)"
            )
        )
        connection.execute(sa.text("INSERT INTO llm_tool_calls VALUES ('legacy', 'attempt-1')"))
        with Operations.context(MigrationContext.configure(connection)):
            module.upgrade()
        row = connection.execute(sa.text("SELECT * FROM llm_tool_calls")).mappings().one()
        assert row["tool_call_id"] == "legacy" and row["output_json"] is None
        connection.execute(
            sa.text(
                "INSERT INTO llm_tool_calls (tool_call_id, attempt_id, provider_tool_call_id) "
                "VALUES ('first', 'attempt-1', 'call-1')"
            )
        )
        with pytest.raises(IntegrityError), connection.begin_nested():
            connection.execute(
                sa.text(
                    "INSERT INTO llm_tool_calls (tool_call_id, attempt_id, provider_tool_call_id) "
                    "VALUES ('duplicate', 'attempt-1', 'call-1')"
                )
            )
        with Operations.context(MigrationContext.configure(connection)):
            module.downgrade()
        assert connection.execute(sa.text("SELECT COUNT(*) FROM llm_tool_calls")).scalar() == 2
