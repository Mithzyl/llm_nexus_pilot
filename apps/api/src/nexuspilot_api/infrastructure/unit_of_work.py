"""Request-scoped SQLAlchemy Unit of Work for composable transactions."""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from nexuspilot_api.repositories.user_repository import UserRepository


class SqlAlchemyUnitOfWork:
    """Coordinate repositories and the single transaction owned by an application use case."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind repositories to the same request-scoped database session."""

        self.session = session
        self.users = UserRepository(session)

    async def flush(self) -> None:
        """Send staged changes to the database without committing the transaction."""

        await self.session.flush()

    async def commit(self) -> None:
        """Commit all repository changes belonging to the current use case."""

        await self.session.commit()

    async def rollback(self) -> None:
        """Discard all uncommitted changes belonging to the current use case."""

        await self.session.rollback()

    async def refresh(self, entity: Any) -> None:
        """Reload a committed entity so database-generated fields are available."""

        await self.session.refresh(entity)
