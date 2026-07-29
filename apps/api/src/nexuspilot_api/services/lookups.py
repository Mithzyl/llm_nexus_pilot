"""Shared entity lookups used by business services."""

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from nexuspilot_api.models import LlmRun, LlmTask


async def require_run(session: AsyncSession, run_id: str) -> LlmRun:
    """Return a run by identifier or raise the stable public HTTP 404 error."""

    run = await session.get(LlmRun, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")
    return run


async def require_task(session: AsyncSession, task_id: str) -> LlmTask:
    """Return a task by identifier or raise the stable public HTTP 404 error."""

    task = await session.get(LlmTask, task_id)
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    return task
