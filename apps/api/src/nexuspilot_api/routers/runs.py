"""Run resource controller."""

from fastapi import APIRouter, status

from nexuspilot_api.routers.common import SessionDependency
from nexuspilot_api.schemas.runs import RunCreate, RunDetail, RunRead
from nexuspilot_api.services.run_service import create_run, get_run_detail

router = APIRouter(tags=["runs"])


@router.post("/runs", response_model=RunRead, status_code=status.HTTP_201_CREATED)
async def post_run(payload: RunCreate, session: SessionDependency) -> RunRead:
    """Create one pending run representing a complete user request."""

    return RunRead.model_validate(await create_run(session, payload))


@router.get("/runs/{run_id}", response_model=RunDetail)
async def get_run(run_id: str, session: SessionDependency) -> RunDetail:
    """Return the run and its complete task, attempt, retry, and artifact history."""

    return RunDetail.model_validate(await get_run_detail(session, run_id))
