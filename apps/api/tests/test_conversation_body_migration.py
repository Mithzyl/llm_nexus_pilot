"""Exercise additive body references and refuse downgrade before verified restore."""

import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext

from nexuspilot_api.infrastructure.conversation_body_fields import BODY_FIELDS


def test_body_migration_preserves_legacy_and_refuses_unsafe_downgrade():
    """Old text survives upgrade; references must be restored before columns may be dropped."""
    path = (
        Path(__file__).parents[1] / "migrations/versions/20261018_0024_conversation_body_objects.py"
    )
    spec = importlib.util.spec_from_file_location("body_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert set(migration.TABLES) == set(BODY_FIELDS)
    with sa.create_engine("sqlite:///:memory:").begin() as connection:
        for table in migration.TABLES:
            connection.execute(sa.text(f"CREATE TABLE {table} (id TEXT PRIMARY KEY, body TEXT)"))
            connection.execute(sa.text(f"INSERT INTO {table} VALUES ('old', 'LEGACY_EXACT')"))
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            for table in migration.TABLES:
                assert connection.execute(
                    sa.text(f"SELECT body, body_ref_json FROM {table}")
                ).one() == ("LEGACY_EXACT", None)
            connection.execute(sa.text("UPDATE llm_messages SET body_ref_json='{}'"))
            with pytest.raises(RuntimeError, match="Restore"):
                migration.downgrade()
            assert "body_ref_json" in {
                c["name"] for c in sa.inspect(connection).get_columns("llm_messages")
            }
            connection.execute(sa.text("UPDATE llm_messages SET body_ref_json=NULL"))
            migration.downgrade()
            assert (
                connection.execute(sa.text("SELECT body FROM llm_messages")).scalar()
                == "LEGACY_EXACT"
            )
