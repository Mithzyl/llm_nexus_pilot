"""Verify the additive retry-map migration and optional real-MySQL concurrent submissions.

The MySQL check requires NEXUSPILOT_TEST_TURN_DATABASE_URL pointing to an isolated
test database already upgraded to head; it creates one owner/session and never
connects to the application's configured database or calls a Provider.
"""

import asyncio
import importlib.util
import os
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from nexuspilot_api.models import LlmConversationTurn, LlmMessage, LlmRun, LlmSession, User
from nexuspilot_api.schemas.sessions import ConversationTurnCreate
from nexuspilot_api.services.session_service import create_conversation_turn


def test_turn_migration_only_adds_retry_map_and_preserves_existing_rows():
    """Upgrade/downgrade adds only the retry map and preserves existing messages and Runs."""

    path = (
        Path(__file__).parents[1]
        / "migrations/versions/20260912_0021_conversation_turn_idempotency.py"
    )
    spec = importlib.util.spec_from_file_location("turn_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(sa.text("PRAGMA foreign_keys=ON"))
        for table, key in (
            ("llm_sessions", "session_id"),
            ("llm_runs", "run_id"),
            ("llm_messages", "message_id"),
        ):
            # Identifiers are fixed test constants, never user-controlled SQL.
            connection.execute(sa.text(f"CREATE TABLE {table} ({key} VARCHAR(36) PRIMARY KEY)"))
            connection.execute(sa.text(f"INSERT INTO {table} VALUES ('existing')"))
        with Operations.context(MigrationContext.configure(connection)):
            module.upgrade()
        columns = {
            item["name"] for item in sa.inspect(connection).get_columns("llm_conversation_turns")
        }
        assert columns == {
            "session_id",
            "key_hash",
            "request_hash",
            "run_id",
            "message_id",
            "created_at",
        }
        assert "key_hash" not in {
            item["name"] for item in sa.inspect(connection).get_columns("llm_runs")
        }
        with Operations.context(MigrationContext.configure(connection)):
            module.downgrade()
        assert set(sa.inspect(connection).get_table_names()) == {
            "llm_sessions",
            "llm_runs",
            "llm_messages",
        }
        assert connection.scalar(sa.text("SELECT COUNT(*) FROM llm_messages")) == 1


@pytest.mark.skipif(
    not os.environ.get("NEXUSPILOT_TEST_TURN_DATABASE_URL"),
    reason="Requires an explicitly configured isolated MySQL test database at head",
)
async def test_mysql_parallel_keyed_turns_commit_once():
    """Independent MySQL connections racing the same key return one Run and one message."""

    engine = create_async_engine(os.environ["NEXUSPILOT_TEST_TURN_DATABASE_URL"])
    factory = async_sessionmaker(engine, expire_on_commit=False)
    owner = f"turn-test-{uuid.uuid4().hex}"
    session_id = str(uuid.uuid4())
    try:
        async with factory() as db:
            db.add(User(user_id=owner, display_name="isolated turn test"))
            await db.flush()
            db.add(LlmSession(session_id=session_id, user_id=owner, title="retry test"))
            await db.commit()

        async def submit():
            """Use a separate transaction for each simultaneous client submission."""

            async with factory() as db:
                run, message, replayed = await create_conversation_turn(
                    db,
                    session_id,
                    ConversationTurnCreate(
                        user_id=owner, content_text="same", idempotency_key="concurrent-key"
                    ),
                )
                return run.run_id, message.message_id, replayed

        results = await asyncio.gather(*(submit() for _ in range(8)))
        assert len({row[0] for row in results}) == len({row[1] for row in results}) == 1
        assert sum(not row[2] for row in results) == 1
        async with factory() as db:
            assert (
                await db.scalar(
                    sa.select(sa.func.count()).select_from(LlmRun).where(LlmRun.user_id == owner)
                )
                == 1
            )
            assert (
                await db.scalar(
                    sa.select(sa.func.count())
                    .select_from(LlmMessage)
                    .where(LlmMessage.session_id == session_id)
                )
                == 1
            )
            conversation = await db.get(LlmSession, session_id)
            assert conversation.next_message_sequence == 2
    finally:
        async with factory() as db:
            await db.execute(
                sa.delete(LlmConversationTurn).where(LlmConversationTurn.session_id == session_id)
            )
            await db.execute(sa.delete(LlmMessage).where(LlmMessage.session_id == session_id))
            await db.execute(sa.delete(LlmRun).where(LlmRun.user_id == owner))
            await db.execute(sa.delete(LlmSession).where(LlmSession.session_id == session_id))
            await db.execute(sa.delete(User).where(User.user_id == owner))
            await db.commit()
        await engine.dispose()
