"""Observe committed Run cancellation across API processes during provider waits."""

import asyncio
from contextlib import asynccontextmanager

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from nexuspilot_api.models import LlmRun
from nexuspilot_api.models.enums import RunStatus


@asynccontextmanager
async def watch_run_cancellation(db: AsyncSession, run_id: str, *, poll_seconds: float = 0.5):
    """Cancel the caller on durable cancellation; close the observer on every exit.

    Read with independent short transactions, never concurrently use the caller's
    session. Closing a local HTTP connection cannot guarantee remote billing stops.
    A failed status read interrupts execution and propagates the actual exception.
    """
    sessions = async_sessionmaker(db.bind, expire_on_commit=False)
    owner = asyncio.current_task()

    async def cancelled():
        """Fetch fresh committed status without retaining a transaction between polls."""
        async with sessions() as session:
            status = await session.scalar(select(LlmRun.status).where(LlmRun.run_id == run_id))
        return status in {RunStatus.CANCEL_REQUESTED, RunStatus.CANCELLED}

    if await cancelled():
        raise asyncio.CancelledError()

    async def monitor():
        """Interrupt the provider wait only after explicit cancellation or observer failure."""
        try:
            while True:
                await asyncio.sleep(poll_seconds)
                if await cancelled():
                    owner.cancel()
                    return
        except Exception:
            owner.cancel()
            raise

    watcher = asyncio.create_task(monitor(), name=f"cancel-watch:{run_id}")
    try:
        yield
    finally:
        watcher.cancel()
        results = await asyncio.gather(watcher, return_exceptions=True)
        if isinstance(results[0], Exception):
            raise results[0]
