"""Async SQLAlchemy engine and request-scoped session management."""

import asyncio
from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from nexuspilot_api.core.config import get_settings

settings = get_settings()
engine = create_async_engine(settings.database_url, pool_pre_ping=True)
session_factory = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    """Yield one request session, roll back failures, and always close the session."""

    async with session_factory() as session:
        try:
            yield session
        except (Exception, asyncio.CancelledError):
            await session.rollback()
            raise
