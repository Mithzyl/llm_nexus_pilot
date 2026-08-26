"""Run resource controller."""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query, status

from nexuspilot_api.routers.common import CursorCodecDependency, DatabaseSessionDependency
from nexuspilot_api.schemas.pagination import CursorPage
from nexuspilot_api.schemas.runs import RunCreate, RunDetail, RunRead, RunStatus, RunSummary
from nexuspilot_api.services.run_service import cancel_run, create_run, get_run_detail, list_runs

router = APIRouter(tags=["runs"])


@router.post("/runs", response_model=RunRead, status_code=status.HTTP_201_CREATED)
async def post_run(payload: RunCreate, db_session: DatabaseSessionDependency) -> RunRead:
    """Create one pending run representing a complete user request."""

    return RunRead.model_validate(await create_run(db_session, payload))


@router.get("/runs", response_model=CursorPage[RunSummary])
async def get_runs(
    db_session: DatabaseSessionDependency,
    cursor_codec: CursorCodecDependency,
    cursor: Annotated[str | None, Query(max_length=2048)] = None,
    user_id: Annotated[str | None, Query(max_length=128)] = None,
    session_id: Annotated[str | None, Query(max_length=36)] = None,
    run_status: Annotated[RunStatus | None, Query(alias="status")] = None,
    created_after: datetime | None = None,
    created_before: datetime | None = None,
    limit: int = Query(default=50, ge=1, le=100),
) -> CursorPage[RunSummary]:
    """List bounded run summaries using query-bound signed cursor pagination."""

    return await list_runs(
        db_session,
        codec=cursor_codec,
        cursor=cursor,
        user_id=user_id,
        session_id=session_id,
        run_status=run_status,
        created_after=created_after,
        created_before=created_before,
        limit=limit,
    )


@router.get("/runs/{run_id}", response_model=RunDetail)
async def get_run(run_id: str, db_session: DatabaseSessionDependency) -> RunDetail:
    """Return a run with bounded compatibility snapshots of its child histories."""

    return RunDetail.model_validate(await get_run_detail(db_session, run_id))


@router.post("/runs/{run_id}/cancel", response_model=RunRead)
async def post_run_cancel(run_id: str, db_session: DatabaseSessionDependency) -> RunRead:
    """Request cancellation through the run and child-task state machines."""

    return RunRead.model_validate(await cancel_run(db_session, run_id))
