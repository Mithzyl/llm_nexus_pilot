"""Transactional run creation and aggregate-query operations."""

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nexuspilot_api.models import (
    LlmArtifact,
    LlmAttempt,
    LlmAttemptRetry,
    LlmRun,
    LlmTask,
    RunStatus,
    User,
)
from nexuspilot_api.schemas.runs import RunCreate
from nexuspilot_api.services.lookups import require_run


async def create_run(session: AsyncSession, payload: RunCreate) -> LlmRun:
    """Persist a new user request after validating that its owner is active."""

    user = await session.get(User, payload.user_id)
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Run user must exist and be active",
        )
    run = LlmRun(**payload.model_dump(), status=RunStatus.PENDING)
    session.add(run)
    await session.commit()
    await session.refresh(run)
    return run


async def get_run_detail(session: AsyncSession, run_id: str) -> dict:
    """Load a run with ordered task, model-attempt, retry, and artifact records."""

    run = await require_run(session, run_id)
    tasks = (
        (
            await session.execute(
                select(LlmTask).where(LlmTask.run_id == run_id).order_by(LlmTask.created_at)
            )
        )
        .scalars()
        .all()
    )
    attempts = (
        (
            await session.execute(
                select(LlmAttempt)
                .where(LlmAttempt.run_id == run_id)
                .order_by(LlmAttempt.started_at)
            )
        )
        .scalars()
        .all()
    )
    attempt_ids = [attempt.attempt_id for attempt in attempts]
    retries = (
        (
            await session.execute(
                select(LlmAttemptRetry)
                .where(LlmAttemptRetry.attempt_id.in_(attempt_ids))
                .order_by(LlmAttemptRetry.attempt_id, LlmAttemptRetry.attempt_index)
            )
        )
        .scalars()
        .all()
        if attempt_ids
        else []
    )
    retries_by_attempt: dict[str, list[LlmAttemptRetry]] = {}
    for retry in retries:
        retries_by_attempt.setdefault(retry.attempt_id, []).append(retry)
    artifacts = (
        (
            await session.execute(
                select(LlmArtifact)
                .where(LlmArtifact.run_id == run_id)
                .order_by(LlmArtifact.created_at)
            )
        )
        .scalars()
        .all()
    )
    attempt_details = [
        {
            **attempt.__dict__,
            "retries": retries_by_attempt.get(attempt.attempt_id, []),
        }
        for attempt in attempts
    ]
    return {
        **run.__dict__,
        "tasks": tasks,
        "attempts": attempt_details,
        "artifacts": artifacts,
    }
