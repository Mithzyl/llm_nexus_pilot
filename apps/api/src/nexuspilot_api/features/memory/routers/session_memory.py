"""L1 Session State and Summary controllers."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status

from nexuspilot_api.core.security import require_internal_api_key
from nexuspilot_api.features.memory.schemas.session_memory import (
    SessionMemoryView,
    SessionStateCreate,
    SessionStateRead,
    SessionSummaryCreate,
    SessionSummaryRead,
)
from nexuspilot_api.features.memory.services.session_memory_service import (
    create_session_state,
    create_session_summary,
    get_session_memory_view,
    list_session_summaries,
)
from nexuspilot_api.routers.common import (
    CursorCodecDependency,
    DatabaseSessionDependency,
    ObjectStorageDependency,
)
from nexuspilot_api.schemas.pagination import CursorPage

router = APIRouter(tags=["session-memory"])
internal_router = APIRouter(
    prefix="/internal/sessions",
    tags=["internal-session-memory"],
    dependencies=[Depends(require_internal_api_key)],
)


@router.get("/sessions/{session_id}/memory", response_model=SessionMemoryView)
async def get_session_memory(
    session_id: str,
    db_session: DatabaseSessionDependency,
) -> SessionMemoryView:
    """Return the current Session State, Summary, and explicit staleness flag."""

    return await get_session_memory_view(db_session, session_id)


@router.post(
    "/sessions/{session_id}/summaries",
    response_model=SessionSummaryRead,
    status_code=status.HTTP_201_CREATED,
)
async def post_session_summary(
    session_id: str,
    payload: SessionSummaryCreate,
    response: Response,
    db_session: DatabaseSessionDependency,
    object_storage: ObjectStorageDependency,
) -> SessionSummaryRead:
    """Generate one immutable Session Summary version and switch the current pointer."""

    result = await create_session_summary(db_session, object_storage, session_id, payload)
    if result.was_replayed:
        response.status_code = status.HTTP_200_OK
    return result.record


@router.get(
    "/sessions/{session_id}/summaries",
    response_model=CursorPage[SessionSummaryRead],
)
async def get_session_summaries(
    session_id: str,
    db_session: DatabaseSessionDependency,
    cursor_codec: CursorCodecDependency,
    cursor: Annotated[str | None, Query(max_length=2048)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> CursorPage[SessionSummaryRead]:
    """List immutable Session Summary versions in ascending version order."""

    return await list_session_summaries(
        db_session,
        session_id,
        codec=cursor_codec,
        cursor=cursor,
        limit=limit,
    )


@internal_router.post(
    "/{session_id}/states",
    response_model=SessionStateRead,
    status_code=status.HTTP_201_CREATED,
)
async def post_session_state(
    session_id: str,
    payload: SessionStateCreate,
    response: Response,
    db_session: DatabaseSessionDependency,
    object_storage: ObjectStorageDependency,
) -> SessionStateRead:
    """Persist one versioned Session State snapshot behind both API-key boundaries."""

    result = await create_session_state(db_session, object_storage, session_id, payload)
    if result.was_replayed:
        response.status_code = status.HTTP_200_OK
    return result.record
