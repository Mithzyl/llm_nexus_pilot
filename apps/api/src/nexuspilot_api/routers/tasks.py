"""Task resource controller."""

from typing import Annotated

from fastapi import APIRouter, Query, status

from nexuspilot_api.routers.common import CursorCodecDependency, DatabaseSessionDependency
from nexuspilot_api.schemas.pagination import CursorPage
from nexuspilot_api.schemas.tasks import (
    TaskCreate,
    TaskRead,
    TaskRetryRead,
    TaskStatus,
    TaskSummary,
)
from nexuspilot_api.services.lookups import require_task
from nexuspilot_api.services.task_service import cancel_task, create_task, list_tasks, retry_task

router = APIRouter(tags=["tasks"])


@router.post("/runs/{run_id}/tasks", response_model=TaskRead, status_code=status.HTTP_201_CREATED)
async def post_task(
    run_id: str,
    payload: TaskCreate,
    db_session: DatabaseSessionDependency,
) -> TaskRead:
    """Create a task inside a run after validating all dependency edges."""

    return TaskRead.model_validate(await create_task(db_session, run_id, payload))


@router.get("/runs/{run_id}/tasks", response_model=CursorPage[TaskSummary])
async def get_run_tasks(
    run_id: str,
    db_session: DatabaseSessionDependency,
    cursor_codec: CursorCodecDependency,
    cursor: Annotated[str | None, Query(max_length=2048)] = None,
    task_status: Annotated[TaskStatus | None, Query(alias="status")] = None,
    task_type: Annotated[str | None, Query(max_length=64)] = None,
    assigned_role: Annotated[str | None, Query(max_length=64)] = None,
    limit: int = Query(default=50, ge=1, le=100),
) -> CursorPage[TaskSummary]:
    """List one run's bounded task summaries using the common task query."""

    return await list_tasks(
        db_session,
        codec=cursor_codec,
        cursor=cursor,
        run_id=run_id,
        task_status=task_status,
        task_type=task_type,
        assigned_role=assigned_role,
        limit=limit,
    )


@router.get("/tasks", response_model=CursorPage[TaskSummary])
async def get_tasks(
    db_session: DatabaseSessionDependency,
    cursor_codec: CursorCodecDependency,
    cursor: Annotated[str | None, Query(max_length=2048)] = None,
    run_id: Annotated[str | None, Query(max_length=36)] = None,
    task_status: Annotated[TaskStatus | None, Query(alias="status")] = None,
    task_type: Annotated[str | None, Query(max_length=64)] = None,
    assigned_role: Annotated[str | None, Query(max_length=64)] = None,
    limit: int = Query(default=50, ge=1, le=100),
) -> CursorPage[TaskSummary]:
    """List bounded task summaries using optional execution and ownership filters."""

    return await list_tasks(
        db_session,
        codec=cursor_codec,
        cursor=cursor,
        run_id=run_id,
        task_status=task_status,
        task_type=task_type,
        assigned_role=assigned_role,
        limit=limit,
    )


@router.get("/tasks/{task_id}", response_model=TaskRead)
async def get_task(task_id: str, db_session: DatabaseSessionDependency) -> TaskRead:
    """Return the latest persisted status and limits for one task."""

    return TaskRead.model_validate(await require_task(db_session, task_id))


@router.post("/tasks/{task_id}/cancel", response_model=TaskRead)
async def post_task_cancel(task_id: str, db_session: DatabaseSessionDependency) -> TaskRead:
    """Cancel one task through its guarded lifecycle transition."""

    return TaskRead.model_validate(await cancel_task(db_session, task_id))


@router.post("/tasks/{task_id}/retry", response_model=TaskRetryRead)
async def post_task_retry(
    task_id: str,
    db_session: DatabaseSessionDependency,
) -> TaskRetryRead:
    """Reschedule one failed task and return its durable retry event identity."""

    return await retry_task(db_session, task_id)
