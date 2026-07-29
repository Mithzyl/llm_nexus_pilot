"""Transactional operations for manually recorded model attempts."""

from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from nexuspilot_api.models import LlmAttempt, LlmRun
from nexuspilot_api.schemas.attempts import AttemptCreate
from nexuspilot_api.services.lookups import require_run, require_task


async def create_attempt(
    session: AsyncSession,
    run_id: str,
    payload: AttemptCreate,
) -> LlmAttempt:
    """Save one model call and add its estimated cost to the owning run atomically."""

    await require_run(session, run_id)
    if payload.task_id:
        task = await require_task(session, payload.task_id)
        if task.run_id != run_id:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Task does not belong to the run",
            )
    attempt = LlmAttempt(run_id=run_id, **payload.model_dump())
    session.add(attempt)
    cost = payload.estimated_cost or Decimal("0")
    await session.execute(
        update(LlmRun).where(LlmRun.run_id == run_id).values(cost_used=LlmRun.cost_used + cost)
    )
    await session.commit()
    await session.refresh(attempt)
    return attempt
