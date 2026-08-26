"""Async SQLAlchemy engine and request-scoped database session management."""

import asyncio
from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from nexuspilot_api.core.config import get_settings

settings = get_settings()
engine = create_async_engine(settings.database_url, pool_pre_ping=True)
database_session_factory = async_sessionmaker(engine, expire_on_commit=False)


def get_database_session_factory() -> async_sessionmaker[AsyncSession]:
    """Return the factory used when work must outlive a request-scoped session."""

    return database_session_factory


async def get_database_session() -> AsyncIterator[AsyncSession]:
    """Yield one request database session, roll back failures, and always close it."""

    async with database_session_factory() as db_session:
        try:
            yield db_session
        except (Exception, asyncio.CancelledError):
            await db_session.rollback()
            raise
