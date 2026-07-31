"""Transactional task operations and dependency validation."""

from dataclasses import dataclass
from datetime import UTC

from fastapi import HTTPException, status
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from nexuspilot_api.core.errors import (
    InvalidCursorError,
    ResourceConflictError,
    ResourceNotFoundError,
)
from nexuspilot_api.core.pagination import (
    CursorCodec,
    DatabaseQueryPaginationKey,
    database_query_fingerprint,
)
from nexuspilot_api.models import (
    LlmOutboxEvent,
    LlmRun,
    LlmTask,
    LlmTaskDependency,
    RunStatus,
    TaskStatus,
    new_id,
)
from nexuspilot_api.models.base import utc_now
from nexuspilot_api.schemas.pagination import CursorPage
from nexuspilot_api.schemas.tasks import TaskCreate, TaskRetryRead, TaskSummary
from nexuspilot_api.services.lookups import require_run


@dataclass(frozen=True)
class TaskDatabasePage:
    """Contain one task query page and its next query-bound database key."""

    items: list[LlmTask]
    next_database_key: DatabaseQueryPaginationKey | None


async def create_task(db_session: AsyncSession, run_id: str, payload: TaskCreate) -> LlmTask:
    """Create a task after confirming its parent and dependencies belong to the run."""

    run = await db_session.scalar(
        select(LlmRun).where(LlmRun.run_id == run_id).with_for_update()
    )
    if run is None:
        raise ResourceNotFoundError("Run")
    if run.status not in {RunStatus.PENDING, RunStatus.RUNNING}:
        raise ResourceConflictError(f"Cannot create a task for run in {run.status.value} status")
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


async def list_tasks(
    db_session: AsyncSession,
    *,
    codec: CursorCodec,
    cursor: str | None,
    run_id: str | None,
    task_status: TaskStatus | None,
    task_type: str | None,
    assigned_role: str | None,
    limit: int,
) -> CursorPage[TaskSummary]:
    """Return a bounded task page with a cursor bound to all normalized filters."""

    if run_id is not None:
        await require_run(db_session, run_id)
    query_fingerprint = database_query_fingerprint(
        "tasks",
        {
            "run_id": run_id,
            "status": task_status.value if task_status else None,
            "task_type": task_type,
            "assigned_role": assigned_role,
        },
    )
    after_database_key = codec.decode_query(cursor) if cursor else None
    if after_database_key and after_database_key.query_fingerprint != query_fingerprint:
        raise InvalidCursorError
    page = await _query_task_database_page(
        db_session,
        run_id=run_id,
        task_status=task_status,
        task_type=task_type,
        assigned_role=assigned_role,
        after_database_key=after_database_key,
        query_fingerprint=query_fingerprint,
        limit=limit,
    )
    next_cursor = (
        codec.encode_query(page.next_database_key) if page.next_database_key else None
    )
    return CursorPage[TaskSummary](
        items=[
            TaskSummary(
                task_id=item.task_id,
                run_id=item.run_id,
                parent_task_id=item.parent_task_id,
                task_type=item.task_type,
                title=item.title,
                objective_preview=item.objective[:200],
                assigned_role=item.assigned_role,
                status=item.status,
                priority=item.priority,
                max_attempts=item.max_attempts,
                current_attempt=item.current_attempt,
                timeout_seconds=item.timeout_seconds,
                started_at=item.started_at,
                completed_at=item.completed_at,
                created_at=item.created_at,
                updated_at=item.updated_at,
            )
            for item in page.items
        ],
        next_cursor=next_cursor,
        has_more=next_cursor is not None,
        limit=limit,
    )


async def cancel_task(db_session: AsyncSession, task_id: str) -> LlmTask:
    """Cancel a task immediately or record a cancellation request when it is running."""

    _, task = await _lock_task_with_run(db_session, task_id)
    if task.status in {TaskStatus.CANCEL_REQUESTED, TaskStatus.CANCELLED}:
        await db_session.commit()
        return task
    if task.status in {TaskStatus.COMPLETED, TaskStatus.FAILED}:
        raise ResourceConflictError(f"Cannot cancel task in {task.status.value} status")
    if task.status == TaskStatus.RUNNING:
        task.status = TaskStatus.CANCEL_REQUESTED
        db_session.add(create_task_control_event(task, event_type="task.cancel_requested"))
    else:
        task.status = TaskStatus.CANCELLED
        task.completed_at = utc_now()
    await db_session.commit()
    await db_session.refresh(task)
    return task


async def retry_task(db_session: AsyncSession, task_id: str) -> TaskRetryRead:
    """Reschedule one failed task and atomically persist its durable retry-request fact."""

    run, task = await _lock_task_with_run(db_session, task_id)
    if run.status not in {RunStatus.PENDING, RunStatus.RUNNING}:
        raise ResourceConflictError(f"Cannot retry a task for run in {run.status.value} status")
    if task.status != TaskStatus.FAILED:
        raise ResourceConflictError(f"Cannot retry task in {task.status.value} status")
    if task.current_attempt >= task.max_attempts:
        raise ResourceConflictError("Task has reached its maximum attempt count")

    task.current_attempt += 1
    task.status = TaskStatus.RETRY_SCHEDULED
    task.started_at = None
    task.completed_at = None
    event = create_task_control_event(
        task,
        event_type="task.retry_requested",
        attempt=task.current_attempt,
    )
    db_session.add(event)
    await db_session.commit()
    await db_session.refresh(task)
    return TaskRetryRead(
        task_id=task.task_id,
        status=task.status,
        current_attempt=task.current_attempt,
        max_attempts=task.max_attempts,
        event_id=event.event_id,
    )


async def _query_task_database_page(
    db_session: AsyncSession,
    *,
    run_id: str | None,
    task_status: TaskStatus | None,
    task_type: str | None,
    assigned_role: str | None,
    after_database_key: DatabaseQueryPaginationKey | None,
    query_fingerprint: str,
    limit: int,
) -> TaskDatabasePage:
    """Query one ordered task page after an optional query-bound database key."""

    statement = select(LlmTask)
    if run_id is not None:
        statement = statement.where(LlmTask.run_id == run_id)
    if task_status is not None:
        statement = statement.where(LlmTask.status == task_status)
    if task_type is not None:
        statement = statement.where(LlmTask.task_type == task_type)
    if assigned_role is not None:
        statement = statement.where(LlmTask.assigned_role == assigned_role)
    if after_database_key is not None:
        cursor_time = after_database_key.created_at.astimezone(UTC).replace(tzinfo=None)
        statement = statement.where(
            or_(
                LlmTask.created_at > cursor_time,
                and_(
                    LlmTask.created_at == cursor_time,
                    LlmTask.task_id > after_database_key.identifier,
                ),
            )
        )
    statement = statement.order_by(LlmTask.created_at, LlmTask.task_id).limit(limit + 1)
    tasks = list((await db_session.scalars(statement)).all())
    has_more = len(tasks) > limit
    items = tasks[:limit]
    next_database_key = None
    if has_more and items:
        last_task = items[-1]
        next_database_key = DatabaseQueryPaginationKey(
            created_at=last_task.created_at,
            identifier=last_task.task_id,
            query_fingerprint=query_fingerprint,
        )
    return TaskDatabasePage(items=items, next_database_key=next_database_key)


async def _lock_task_with_run(
    db_session: AsyncSession,
    task_id: str,
) -> tuple[LlmRun, LlmTask]:
    """Lock a Task through its owning Run using the platform-wide lock order."""

    run_id = await db_session.scalar(
        select(LlmTask.run_id).where(LlmTask.task_id == task_id)
    )
    if run_id is None:
        raise ResourceNotFoundError("Task")
    run = await db_session.scalar(
        select(LlmRun).where(LlmRun.run_id == run_id).with_for_update()
    )
    task = await db_session.scalar(
        select(LlmTask).where(LlmTask.task_id == task_id).with_for_update()
    )
    if run is None or task is None:
        raise ResourceNotFoundError("Task")
    return run, task


def create_task_control_event(
    task: LlmTask,
    *,
    event_type: str,
    attempt: int | None = None,
) -> LlmOutboxEvent:
    """Create an unpublished task-control fact for a later reliable queue publisher."""

    event_id = new_id()
    payload = {
        "message_id": event_id,
        "task_id": task.task_id,
        "run_id": task.run_id,
        "event_type": event_type,
    }
    if attempt is not None:
        payload["attempt"] = attempt
    return LlmOutboxEvent(
        event_id=event_id,
        aggregate_type="task",
        aggregate_id=task.task_id,
        event_type=event_type,
        payload_json=payload,
        status="pending",
        publish_attempts=0,
    )
