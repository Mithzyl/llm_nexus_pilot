"""Database transaction tests for user service operations."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from nexuspilot_api.core.errors import ResourceConflictError
from nexuspilot_api.models import User
from nexuspilot_api.schemas.users import UserCreate
from nexuspilot_api.services.user_service import create_user


async def test_database_session_changes_can_be_rolled_back(
    test_database_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Verify request database changes can be flushed and rolled back atomically."""

    async with test_database_session_factory() as db_session:
        db_session.add(User(user_id="rollback-user", display_name="Rollback"))
        await db_session.flush()
        await db_session.rollback()

    async with test_database_session_factory() as verification_db_session:
        assert await verification_db_session.get(User, "rollback-user") is None


async def test_database_session_commit_persists_changes(
    test_database_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Verify one database session commit makes all staged changes durable."""

    async with test_database_session_factory() as db_session:
        db_session.add(User(user_id="committed-user", display_name="Committed"))
        await db_session.commit()

    async with test_database_session_factory() as verification_db_session:
        user = await verification_db_session.get(User, "committed-user")
        assert user is not None
        assert user.display_name == "Committed"


async def test_database_unique_constraint_handles_create_race(
    test_database_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify the database constraint remains the final duplicate-user protection."""

    async with test_database_session_factory() as setup_db_session:
        setup_db_session.add(User(user_id="racing-user", display_name="Existing"))
        await setup_db_session.commit()

    async with test_database_session_factory() as db_session:

        async def simulate_stale_precheck(_model: type[User], _user_id: str) -> None:
            """Simulate another transaction inserting after the service pre-check."""

            return None

        monkeypatch.setattr(db_session, "get", simulate_stale_precheck)
        with pytest.raises(ResourceConflictError, match="User already exists"):
            await create_user(
                db_session,
                UserCreate(user_id="racing-user", display_name="Concurrent"),
            )

        assert db_session.in_transaction() is False
