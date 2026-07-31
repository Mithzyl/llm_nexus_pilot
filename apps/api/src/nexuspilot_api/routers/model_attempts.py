"""LLM model invocation and provider transport-attempt resource controller."""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query, status

from nexuspilot_api.routers.common import CursorCodecDependency, DatabaseSessionDependency
from nexuspilot_api.schemas.model_attempts import (
    AttemptStatus,
    ModelAttemptCreate,
    ModelAttemptDetail,
    ModelAttemptRead,
    ModelAttemptSummary,
    ModelTransportAttemptDetail,
    ModelTransportAttemptSummary,
)
from nexuspilot_api.schemas.pagination import CursorPage
from nexuspilot_api.services.model_attempt_service import (
    create_model_attempt,
    get_model_attempt,
    get_model_transport_attempt,
    list_model_attempts,
    list_model_transport_attempts,
)

router = APIRouter(tags=["model-attempts"])


@router.get("/attempts", response_model=CursorPage[ModelAttemptSummary])
async def get_model_attempts(
    db_session: DatabaseSessionDependency,
    cursor_codec: CursorCodecDependency,
    cursor: Annotated[str | None, Query(max_length=2048)] = None,
    run_id: Annotated[str | None, Query(max_length=36)] = None,
    task_id: Annotated[str | None, Query(max_length=36)] = None,
    provider: Annotated[str | None, Query(min_length=1, max_length=64)] = None,
    model: Annotated[str | None, Query(min_length=1, max_length=128)] = None,
    attempt_status: Annotated[AttemptStatus | None, Query(alias="status")] = None,
    started_after: datetime | None = None,
    started_before: datetime | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> CursorPage[ModelAttemptSummary]:
    """List owner-scoped LLM model invocation summaries with signed cursors."""

    return await list_model_attempts(
        db_session,
        codec=cursor_codec,
        cursor=cursor,
        run_id=run_id,
        task_id=task_id,
        provider=provider,
        model=model,
        attempt_status=attempt_status,
        started_after=started_after,
        started_before=started_before,
        limit=limit,
    )


@router.get("/attempts/{attempt_id}", response_model=ModelAttemptDetail)
async def get_model_attempt_detail(
    attempt_id: str,
    db_session: DatabaseSessionDependency,
) -> ModelAttemptDetail:
    """Return safe details for one logical model invocation."""

    return await get_model_attempt(db_session, attempt_id)


@router.get(
    "/attempts/{attempt_id}/retries",
    response_model=CursorPage[ModelTransportAttemptSummary],
)
async def get_model_transport_attempts(
    attempt_id: str,
    db_session: DatabaseSessionDependency,
    cursor_codec: CursorCodecDependency,
    cursor: Annotated[str | None, Query(max_length=2048)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> CursorPage[ModelTransportAttemptSummary]:
    """List provider HTTP transport attempts under one LLM model invocation."""

    return await list_model_transport_attempts(
        db_session,
        attempt_id,
        codec=cursor_codec,
        cursor=cursor,
        limit=limit,
    )


@router.get(
    "/attempt-retries/{retry_id}",
    response_model=ModelTransportAttemptDetail,
)
async def get_model_transport_attempt_detail(
    retry_id: str,
    db_session: DatabaseSessionDependency,
) -> ModelTransportAttemptDetail:
    """Return safe details for one physical provider request."""

    return await get_model_transport_attempt(db_session, retry_id)


@router.post(
    "/runs/{run_id}/attempts",
    response_model=ModelAttemptRead,
    status_code=status.HTTP_201_CREATED,
)
async def post_model_attempt(
    run_id: str,
    payload: ModelAttemptCreate,
    db_session: DatabaseSessionDependency,
) -> ModelAttemptRead:
    """Persist an externally completed model call and update run-level estimated cost."""

    model_attempt = await create_model_attempt(db_session, run_id, payload)
    return ModelAttemptRead.model_validate(model_attempt)
