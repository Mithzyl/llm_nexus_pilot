"""Task resource controller."""

from fastapi import APIRouter, status

from nexuspilot_api.routers.common import SessionDependency
from nexuspilot_api.schemas.tasks import TaskCreate, TaskRead
from nexuspilot_api.services.lookups import require_task
from nexuspilot_api.services.task_service import create_task

router = APIRouter(tags=["tasks"])


@router.post("/runs/{run_id}/tasks", response_model=TaskRead, status_code=status.HTTP_201_CREATED)
async def post_task(
    run_id: str,
    payload: TaskCreate,
    session: SessionDependency,
) -> TaskRead:
    """Create a task inside a run after validating all dependency edges."""

    return TaskRead.model_validate(await create_task(session, run_id, payload))


@router.get("/tasks/{task_id}", response_model=TaskRead)
async def get_task(task_id: str, session: SessionDependency) -> TaskRead:
    """Return the latest persisted status and limits for one task."""

    return TaskRead.model_validate(await require_task(session, task_id))
