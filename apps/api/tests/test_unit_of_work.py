"""Database tests for repository and Unit of Work transaction ownership."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from nexuspilot_api.core.errors import ResourceConflictError
from nexuspilot_api.infrastructure.unit_of_work import SqlAlchemyUnitOfWork
from nexuspilot_api.models import User
from nexuspilot_api.schemas.users import UserCreate
from nexuspilot_api.services.user_service import create_user


async def test_repository_changes_can_be_rolled_back(
    test_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Verify a repository stages changes without committing outside the Unit of Work."""

    async with test_session_factory() as session:
        uow = SqlAlchemyUnitOfWork(session)
        uow.users.add(User(user_id="rollback-user", display_name="Rollback"))
        await uow.flush()
        await uow.rollback()

    async with test_session_factory() as verification_session:
        verification_uow = SqlAlchemyUnitOfWork(verification_session)
        assert await verification_uow.users.get("rollback-user") is None


async def test_unit_of_work_commits_repository_changes(
    test_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Verify one Unit of Work commit makes all staged repository changes durable."""

    async with test_session_factory() as session:
        uow = SqlAlchemyUnitOfWork(session)
        uow.users.add(User(user_id="committed-user", display_name="Committed"))
        await uow.commit()

    async with test_session_factory() as verification_session:
        verification_uow = SqlAlchemyUnitOfWork(verification_session)
        user = await verification_uow.users.get("committed-user")
        assert user is not None
        assert user.display_name == "Committed"


async def test_database_unique_constraint_handles_create_race(
    test_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify the database constraint remains the final duplicate-user protection."""

    async with test_session_factory() as setup_session:
        setup_uow = SqlAlchemyUnitOfWork(setup_session)
        setup_uow.users.add(User(user_id="racing-user", display_name="Existing"))
        await setup_uow.commit()

    async with test_session_factory() as session:
        uow = SqlAlchemyUnitOfWork(session)

        async def simulate_stale_precheck(_user_id: str) -> None:
            """Simulate another transaction inserting after the service pre-check."""

            return None

        monkeypatch.setattr(uow.users, "get", simulate_stale_precheck)
        with pytest.raises(ResourceConflictError, match="User already exists"):
            await create_user(
                uow,
                UserCreate(user_id="racing-user", display_name="Concurrent"),
            )

        assert session.in_transaction() is False
