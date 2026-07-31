"""Strictly authenticated read-only controllers for internal audit facts."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from nexuspilot_api.core.security import require_internal_api_key
from nexuspilot_api.routers.common import CursorCodecDependency, DatabaseSessionDependency
from nexuspilot_api.schemas.internal_audit import (
    ModelToolCallDetail,
    ModelToolCallSummary,
    OutboxEventDetail,
    OutboxEventSummary,
    TaskEvaluationDetail,
    TaskEvaluationSummary,
)
from nexuspilot_api.schemas.pagination import CursorPage
from nexuspilot_api.services.internal_audit_service import (
    get_model_tool_call,
    get_outbox_event,
    get_task_evaluation,
    list_model_tool_calls,
    list_outbox_events,
    list_task_evaluations,
)

router = APIRouter(
    prefix="/internal",
    tags=["internal-audit"],
    dependencies=[Depends(require_internal_api_key)],
)


@router.get("/tool-calls", response_model=CursorPage[ModelToolCallSummary])
async def get_internal_model_tool_calls(
    db_session: DatabaseSessionDependency,
    cursor_codec: CursorCodecDependency,
    cursor: Annotated[str | None, Query(max_length=2048)] = None,
    model_attempt_id: Annotated[
        str | None,
        Query(alias="attempt_id", max_length=36),
    ] = None,
    run_id: Annotated[str | None, Query(max_length=36)] = None,
    task_id: Annotated[str | None, Query(max_length=36)] = None,
    tool_name: Annotated[str | None, Query(min_length=1, max_length=128)] = None,
    risk_level: Annotated[str | None, Query(min_length=1, max_length=32)] = None,
    tool_call_status: Annotated[
        str | None,
        Query(alias="status", min_length=1, max_length=32),
    ] = None,
    permission_decision: Annotated[
        str | None,
        Query(min_length=1, max_length=32),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> CursorPage[ModelToolCallSummary]:
    """List redacted model tool-call metadata behind both API-key boundaries."""

    return await list_model_tool_calls(
        db_session,
        codec=cursor_codec,
        cursor=cursor,
        model_attempt_id=model_attempt_id,
        run_id=run_id,
        task_id=task_id,
        tool_name=tool_name,
        risk_level=risk_level,
        tool_call_status=tool_call_status,
        permission_decision=permission_decision,
        limit=limit,
    )


@router.get("/tool-calls/{tool_call_id}", response_model=ModelToolCallDetail)
async def get_internal_model_tool_call_detail(
    tool_call_id: str,
    db_session: DatabaseSessionDependency,
) -> ModelToolCallDetail:
    """Return one redacted model tool-call audit record."""

    return await get_model_tool_call(db_session, tool_call_id)


@router.get("/evaluations", response_model=CursorPage[TaskEvaluationSummary])
async def get_internal_task_evaluations(
    db_session: DatabaseSessionDependency,
    cursor_codec: CursorCodecDependency,
    cursor: Annotated[str | None, Query(max_length=2048)] = None,
    run_id: Annotated[str | None, Query(max_length=36)] = None,
    task_id: Annotated[str | None, Query(max_length=36)] = None,
    evaluation_type: Annotated[
        str | None,
        Query(min_length=1, max_length=64),
    ] = None,
    verdict: Annotated[str | None, Query(min_length=1, max_length=32)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> CursorPage[TaskEvaluationSummary]:
    """List bounded Task evaluation metadata behind the internal key boundary."""

    return await list_task_evaluations(
        db_session,
        codec=cursor_codec,
        cursor=cursor,
        run_id=run_id,
        task_id=task_id,
        evaluation_type=evaluation_type,
        verdict=verdict,
        limit=limit,
    )


@router.get("/evaluations/{evaluation_id}", response_model=TaskEvaluationDetail)
async def get_internal_task_evaluation_detail(
    evaluation_id: str,
    db_session: DatabaseSessionDependency,
) -> TaskEvaluationDetail:
    """Return one Task evaluation with redacted findings."""

    return await get_task_evaluation(db_session, evaluation_id)


@router.get("/outbox-events", response_model=CursorPage[OutboxEventSummary])
async def get_internal_outbox_events(
    db_session: DatabaseSessionDependency,
    cursor_codec: CursorCodecDependency,
    cursor: Annotated[str | None, Query(max_length=2048)] = None,
    aggregate_type: Annotated[str | None, Query(min_length=1, max_length=64)] = None,
    aggregate_id: Annotated[str | None, Query(min_length=1, max_length=36)] = None,
    event_type: Annotated[str | None, Query(min_length=1, max_length=128)] = None,
    outbox_status: Annotated[
        str | None,
        Query(alias="status", min_length=1, max_length=32),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> CursorPage[OutboxEventSummary]:
    """List infrastructure outbox delivery facts without publishing messages."""

    return await list_outbox_events(
        db_session,
        codec=cursor_codec,
        cursor=cursor,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        event_type=event_type,
        outbox_status=outbox_status,
        limit=limit,
    )


@router.get("/outbox-events/{event_id}", response_model=OutboxEventDetail)
async def get_internal_outbox_event_detail(
    event_id: str,
    db_session: DatabaseSessionDependency,
) -> OutboxEventDetail:
    """Return one infrastructure outbox event with a redacted payload."""

    return await get_outbox_event(db_session, event_id)
