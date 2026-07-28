"""Transactional business operations for runs, tasks, attempts, and artifacts."""

from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from nexuspilot_api.models import (
    LlmArtifact,
    LlmAttempt,
    LlmAttemptRetry,
    LlmRun,
    LlmTask,
    LlmTaskDependency,
    RunStatus,
    TaskStatus,
    User,
)
from nexuspilot_api.schemas import AttemptCreate, RunCreate, TaskCreate, UserCreate


async def create_user(session: AsyncSession, payload: UserCreate) -> User:
    """Create a basic active user and reject duplicate stable identifiers."""

    if await session.get(User, payload.user_id):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="User already exists")
    user = User(**payload.model_dump())
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


async def require_run(session: AsyncSession, run_id: str) -> LlmRun:
    """Return a run by identifier or raise a stable HTTP 404 error."""

    run = await session.get(LlmRun, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")
    return run


async def require_task(session: AsyncSession, task_id: str) -> LlmTask:
    """Return a task by identifier or raise a stable HTTP 404 error."""

    task = await session.get(LlmTask, task_id)
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    return task


async def create_run(session: AsyncSession, payload: RunCreate) -> LlmRun:
    """Persist a new user request in pending state and commit it atomically."""

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


async def create_task(session: AsyncSession, run_id: str, payload: TaskCreate) -> LlmTask:
    """Create a task after confirming its parent and dependencies belong to the same run."""

    await require_run(session, run_id)
    related_ids = set(payload.depends_on_task_ids)
    if payload.parent_task_id:
        related_ids.add(payload.parent_task_id)
    if related_ids:
        result = await session.execute(select(LlmTask).where(LlmTask.task_id.in_(related_ids)))
        related_tasks = {task.task_id: task for task in result.scalars()}
        if related_ids != set(related_tasks) or any(
            task.run_id != run_id for task in related_tasks.values()
        ):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Parent and dependency tasks must exist in the same run",
            )

    task_values = payload.model_dump(exclude={"depends_on_task_ids"})
    initial_status = (
        TaskStatus.WAITING_FOR_DEPENDENCY if payload.depends_on_task_ids else TaskStatus.PENDING
    )
    task = LlmTask(run_id=run_id, status=initial_status, **task_values)
    session.add(task)
    await session.flush()
    session.add_all(
        LlmTaskDependency(task_id=task.task_id, depends_on_task_id=dependency_id)
        for dependency_id in payload.depends_on_task_ids
    )
    await session.commit()
    await session.refresh(task)
    return task


async def create_attempt(session: AsyncSession, run_id: str, payload: AttemptCreate) -> LlmAttempt:
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


async def get_run_detail(session: AsyncSession, run_id: str) -> dict:
    """Load a run with ordered task, model-attempt, and artifact records."""

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
