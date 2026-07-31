"""Transactional task operations and dependency validation."""

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nexuspilot_api.models import LlmTask, LlmTaskDependency, TaskStatus
from nexuspilot_api.schemas.tasks import TaskCreate
from nexuspilot_api.services.lookups import require_run


async def create_task(db_session: AsyncSession, run_id: str, payload: TaskCreate) -> LlmTask:
    """Create a task after confirming its parent and dependencies belong to the run."""

    await require_run(db_session, run_id)
    related_ids = set(payload.depends_on_task_ids)
    if payload.parent_task_id:
        related_ids.add(payload.parent_task_id)
    if related_ids:
        result = await db_session.execute(select(LlmTask).where(LlmTask.task_id.in_(related_ids)))
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
    db_session.add(task)
    await db_session.flush()
    db_session.add_all(
        LlmTaskDependency(task_id=task.task_id, depends_on_task_id=dependency_id)
        for dependency_id in payload.depends_on_task_ids
    )
    await db_session.commit()
    await db_session.refresh(task)
    return task
