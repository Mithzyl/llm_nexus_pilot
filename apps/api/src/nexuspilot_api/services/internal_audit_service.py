"""Read-only, redacted queries for Phase 1 internal audit resources."""

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from nexuspilot_api.core.audit_redaction import redact_audit_json
from nexuspilot_api.core.errors import (
    InvalidCursorError,
    InvalidRequestError,
    ResourceNotFoundError,
)
from nexuspilot_api.core.pagination import (
    CursorCodec,
    DatabaseQueryPaginationKey,
    database_query_fingerprint,
)
from nexuspilot_api.models import (
    LlmModelAttempt,
    LlmModelToolCall,
    LlmOutboxEvent,
    LlmTaskEvaluation,
)
from nexuspilot_api.schemas.internal_audit import (
    ModelToolCallDetail,
    ModelToolCallSummary,
    OutboxEventDetail,
    OutboxEventSummary,
    TaskEvaluationDetail,
    TaskEvaluationSummary,
)
from nexuspilot_api.schemas.pagination import CursorPage
from nexuspilot_api.services.lookups import require_run, require_task


@dataclass(frozen=True)
class ModelToolCallDatabasePage:
    """Contain model tool-call records and their next query-bound database key."""

    items: list[LlmModelToolCall]
    next_database_key: DatabaseQueryPaginationKey | None


@dataclass(frozen=True)
class TaskEvaluationDatabasePage:
    """Contain Task evaluation records and their next query-bound database key."""

    items: list[LlmTaskEvaluation]
    next_database_key: DatabaseQueryPaginationKey | None


@dataclass(frozen=True)
class OutboxEventDatabasePage:
    """Contain infrastructure outbox events and their next query-bound database key."""

    items: list[LlmOutboxEvent]
    next_database_key: DatabaseQueryPaginationKey | None


async def get_model_tool_call(
    db_session: AsyncSession,
    tool_call_id: str,
) -> ModelToolCallDetail:
    """Return one model tool call with redacted input and no result-storage URI."""

    model_tool_call = await db_session.get(LlmModelToolCall, tool_call_id)
    if model_tool_call is None:
        raise ResourceNotFoundError("Model tool call")
    return _model_tool_call_detail(model_tool_call)


async def list_model_tool_calls(
    db_session: AsyncSession,
    *,
    codec: CursorCodec,
    cursor: str | None,
    model_attempt_id: str | None,
    run_id: str | None,
    task_id: str | None,
    tool_name: str | None,
    risk_level: str | None,
    tool_call_status: str | None,
    permission_decision: str | None,
    limit: int,
) -> CursorPage[ModelToolCallSummary]:
    """Return a query-bound internal page of model tool-call audit metadata."""

    await _validate_model_tool_call_scope(
        db_session,
        model_attempt_id=model_attempt_id,
        run_id=run_id,
        task_id=task_id,
    )
    query_fingerprint = database_query_fingerprint(
        "internal_model_tool_calls",
        {
            "model_attempt_id": model_attempt_id,
            "run_id": run_id,
            "task_id": task_id,
            "tool_name": tool_name,
            "risk_level": risk_level,
            "status": tool_call_status,
            "permission_decision": permission_decision,
        },
    )
    after_database_key = _decode_query_cursor(codec, cursor, query_fingerprint)
    database_page = await _query_model_tool_call_database_page(
        db_session,
        model_attempt_id=model_attempt_id,
        run_id=run_id,
        task_id=task_id,
        tool_name=tool_name,
        risk_level=risk_level,
        tool_call_status=tool_call_status,
        permission_decision=permission_decision,
        after_database_key=after_database_key,
        query_fingerprint=query_fingerprint,
        limit=limit,
    )
    next_cursor = _encode_query_cursor(codec, database_page.next_database_key)
    return CursorPage[ModelToolCallSummary](
        items=[
            _model_tool_call_summary(model_tool_call)
            for model_tool_call in database_page.items
        ],
        next_cursor=next_cursor,
        has_more=next_cursor is not None,
        limit=limit,
    )


async def get_task_evaluation(
    db_session: AsyncSession,
    evaluation_id: str,
) -> TaskEvaluationDetail:
    """Return one Task evaluation with bounded and redacted findings."""

    task_evaluation = await db_session.get(LlmTaskEvaluation, evaluation_id)
    if task_evaluation is None:
        raise ResourceNotFoundError("Task evaluation")
    return _task_evaluation_detail(task_evaluation)


async def list_task_evaluations(
    db_session: AsyncSession,
    *,
    codec: CursorCodec,
    cursor: str | None,
    run_id: str | None,
    task_id: str | None,
    evaluation_type: str | None,
    verdict: str | None,
    limit: int,
) -> CursorPage[TaskEvaluationSummary]:
    """Return a query-bound internal page of Task evaluation metadata."""

    await _validate_run_task_scope(db_session, run_id=run_id, task_id=task_id)
    query_fingerprint = database_query_fingerprint(
        "internal_task_evaluations",
        {
            "run_id": run_id,
            "task_id": task_id,
            "evaluation_type": evaluation_type,
            "verdict": verdict,
        },
    )
    after_database_key = _decode_query_cursor(codec, cursor, query_fingerprint)
    database_page = await _query_task_evaluation_database_page(
        db_session,
        run_id=run_id,
        task_id=task_id,
        evaluation_type=evaluation_type,
        verdict=verdict,
        after_database_key=after_database_key,
        query_fingerprint=query_fingerprint,
        limit=limit,
    )
    next_cursor = _encode_query_cursor(codec, database_page.next_database_key)
    return CursorPage[TaskEvaluationSummary](
        items=[
            TaskEvaluationSummary.model_validate(task_evaluation)
            for task_evaluation in database_page.items
        ],
        next_cursor=next_cursor,
        has_more=next_cursor is not None,
        limit=limit,
    )


async def get_outbox_event(
    db_session: AsyncSession,
    event_id: str,
) -> OutboxEventDetail:
    """Return one infrastructure outbox event with a redacted bounded payload."""

    outbox_event = await db_session.get(LlmOutboxEvent, event_id)
    if outbox_event is None:
        raise ResourceNotFoundError("Outbox event")
    return _outbox_event_detail(outbox_event)


async def list_outbox_events(
    db_session: AsyncSession,
    *,
    codec: CursorCodec,
    cursor: str | None,
    aggregate_type: str | None,
    aggregate_id: str | None,
    event_type: str | None,
    outbox_status: str | None,
    limit: int,
) -> CursorPage[OutboxEventSummary]:
    """Return a query-bound internal page of infrastructure delivery facts."""

    query_fingerprint = database_query_fingerprint(
        "internal_outbox_events",
        {
            "aggregate_type": aggregate_type,
            "aggregate_id": aggregate_id,
            "event_type": event_type,
            "status": outbox_status,
        },
    )
    after_database_key = _decode_query_cursor(codec, cursor, query_fingerprint)
    database_page = await _query_outbox_event_database_page(
        db_session,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        event_type=event_type,
        outbox_status=outbox_status,
        after_database_key=after_database_key,
        query_fingerprint=query_fingerprint,
        limit=limit,
    )
    next_cursor = _encode_query_cursor(codec, database_page.next_database_key)
    return CursorPage[OutboxEventSummary](
        items=[
            OutboxEventSummary.model_validate(outbox_event)
            for outbox_event in database_page.items
        ],
        next_cursor=next_cursor,
        has_more=next_cursor is not None,
        limit=limit,
    )


async def _validate_model_tool_call_scope(
    db_session: AsyncSession,
    *,
    model_attempt_id: str | None,
    run_id: str | None,
    task_id: str | None,
) -> None:
    """Validate optional model-attempt, Run, and Task filters describe one scope."""

    await _validate_run_task_scope(db_session, run_id=run_id, task_id=task_id)
    if model_attempt_id is None:
        return
    model_attempt = await db_session.get(LlmModelAttempt, model_attempt_id)
    if model_attempt is None:
        raise ResourceNotFoundError("Model attempt")
    if run_id is not None and model_attempt.run_id != run_id:
        raise InvalidRequestError("Model attempt does not belong to the run")
    if task_id is not None and model_attempt.task_id != task_id:
        raise InvalidRequestError("Model attempt does not belong to the task")


async def _validate_run_task_scope(
    db_session: AsyncSession,
    *,
    run_id: str | None,
    task_id: str | None,
) -> None:
    """Validate optional Run and Task filters without requiring either one."""

    if run_id is not None:
        await require_run(db_session, run_id)
    if task_id is not None:
        task = await require_task(db_session, task_id)
        if run_id is not None and task.run_id != run_id:
            raise InvalidRequestError("Task does not belong to the run")


async def _query_model_tool_call_database_page(
    db_session: AsyncSession,
    *,
    model_attempt_id: str | None,
    run_id: str | None,
    task_id: str | None,
    tool_name: str | None,
    risk_level: str | None,
    tool_call_status: str | None,
    permission_decision: str | None,
    after_database_key: DatabaseQueryPaginationKey | None,
    query_fingerprint: str,
    limit: int,
) -> ModelToolCallDatabasePage:
    """Query one stable model tool-call page after an optional database key."""

    statement = select(LlmModelToolCall)
    if run_id is not None or task_id is not None:
        statement = statement.join(
            LlmModelAttempt,
            LlmModelAttempt.attempt_id == LlmModelToolCall.attempt_id,
        )
    if model_attempt_id is not None:
        statement = statement.where(LlmModelToolCall.attempt_id == model_attempt_id)
    if run_id is not None:
        statement = statement.where(LlmModelAttempt.run_id == run_id)
    if task_id is not None:
        statement = statement.where(LlmModelAttempt.task_id == task_id)
    if tool_name is not None:
        statement = statement.where(LlmModelToolCall.tool_name == tool_name)
    if risk_level is not None:
        statement = statement.where(LlmModelToolCall.risk_level == risk_level)
    if tool_call_status is not None:
        statement = statement.where(LlmModelToolCall.status == tool_call_status)
    if permission_decision is not None:
        statement = statement.where(
            LlmModelToolCall.permission_decision == permission_decision
        )
    if after_database_key is not None:
        cursor_time = _database_time(after_database_key)
        statement = statement.where(
            or_(
                LlmModelToolCall.started_at > cursor_time,
                and_(
                    LlmModelToolCall.started_at == cursor_time,
                    LlmModelToolCall.tool_call_id > after_database_key.identifier,
                ),
            )
        )
    statement = statement.order_by(
        LlmModelToolCall.started_at,
        LlmModelToolCall.tool_call_id,
    ).limit(limit + 1)
    model_tool_calls = list((await db_session.scalars(statement)).all())
    items = model_tool_calls[:limit]
    next_database_key = None
    if len(model_tool_calls) > limit and items:
        last_model_tool_call = items[-1]
        next_database_key = DatabaseQueryPaginationKey(
            created_at=last_model_tool_call.started_at,
            identifier=last_model_tool_call.tool_call_id,
            query_fingerprint=query_fingerprint,
        )
    return ModelToolCallDatabasePage(items=items, next_database_key=next_database_key)


async def _query_task_evaluation_database_page(
    db_session: AsyncSession,
    *,
    run_id: str | None,
    task_id: str | None,
    evaluation_type: str | None,
    verdict: str | None,
    after_database_key: DatabaseQueryPaginationKey | None,
    query_fingerprint: str,
    limit: int,
) -> TaskEvaluationDatabasePage:
    """Query one stable Task evaluation page after an optional database key."""

    statement = select(LlmTaskEvaluation)
    if run_id is not None:
        statement = statement.where(LlmTaskEvaluation.run_id == run_id)
    if task_id is not None:
        statement = statement.where(LlmTaskEvaluation.task_id == task_id)
    if evaluation_type is not None:
        statement = statement.where(
            LlmTaskEvaluation.evaluation_type == evaluation_type
        )
    if verdict is not None:
        statement = statement.where(LlmTaskEvaluation.verdict == verdict)
    if after_database_key is not None:
        cursor_time = _database_time(after_database_key)
        statement = statement.where(
            or_(
                LlmTaskEvaluation.created_at > cursor_time,
                and_(
                    LlmTaskEvaluation.created_at == cursor_time,
                    LlmTaskEvaluation.evaluation_id > after_database_key.identifier,
                ),
            )
        )
    statement = statement.order_by(
        LlmTaskEvaluation.created_at,
        LlmTaskEvaluation.evaluation_id,
    ).limit(limit + 1)
    task_evaluations = list((await db_session.scalars(statement)).all())
    items = task_evaluations[:limit]
    next_database_key = None
    if len(task_evaluations) > limit and items:
        last_task_evaluation = items[-1]
        next_database_key = DatabaseQueryPaginationKey(
            created_at=last_task_evaluation.created_at,
            identifier=last_task_evaluation.evaluation_id,
            query_fingerprint=query_fingerprint,
        )
    return TaskEvaluationDatabasePage(items=items, next_database_key=next_database_key)


async def _query_outbox_event_database_page(
    db_session: AsyncSession,
    *,
    aggregate_type: str | None,
    aggregate_id: str | None,
    event_type: str | None,
    outbox_status: str | None,
    after_database_key: DatabaseQueryPaginationKey | None,
    query_fingerprint: str,
    limit: int,
) -> OutboxEventDatabasePage:
    """Query one stable infrastructure outbox page after an optional database key."""

    statement = select(LlmOutboxEvent)
    if aggregate_type is not None:
        statement = statement.where(LlmOutboxEvent.aggregate_type == aggregate_type)
    if aggregate_id is not None:
        statement = statement.where(LlmOutboxEvent.aggregate_id == aggregate_id)
    if event_type is not None:
        statement = statement.where(LlmOutboxEvent.event_type == event_type)
    if outbox_status is not None:
        statement = statement.where(LlmOutboxEvent.status == outbox_status)
    if after_database_key is not None:
        cursor_time = _database_time(after_database_key)
        statement = statement.where(
            or_(
                LlmOutboxEvent.created_at > cursor_time,
                and_(
                    LlmOutboxEvent.created_at == cursor_time,
                    LlmOutboxEvent.event_id > after_database_key.identifier,
                ),
            )
        )
    statement = statement.order_by(
        LlmOutboxEvent.created_at,
        LlmOutboxEvent.event_id,
    ).limit(limit + 1)
    outbox_events = list((await db_session.scalars(statement)).all())
    items = outbox_events[:limit]
    next_database_key = None
    if len(outbox_events) > limit and items:
        last_outbox_event = items[-1]
        next_database_key = DatabaseQueryPaginationKey(
            created_at=last_outbox_event.created_at,
            identifier=last_outbox_event.event_id,
            query_fingerprint=query_fingerprint,
        )
    return OutboxEventDatabasePage(items=items, next_database_key=next_database_key)


def _model_tool_call_summary(model_tool_call: LlmModelToolCall) -> ModelToolCallSummary:
    """Map a model tool call to metadata that excludes inputs and storage locations."""

    return ModelToolCallSummary(
        tool_call_id=model_tool_call.tool_call_id,
        attempt_id=model_tool_call.attempt_id,
        tool_name=model_tool_call.tool_name,
        risk_level=model_tool_call.risk_level,
        status=model_tool_call.status,
        permission_decision=model_tool_call.permission_decision,
        has_result=model_tool_call.result_uri is not None,
        started_at=model_tool_call.started_at,
        completed_at=model_tool_call.completed_at,
    )


def _model_tool_call_detail(model_tool_call: LlmModelToolCall) -> ModelToolCallDetail:
    """Map a model tool call to a redacted and bounded internal detail response."""

    return ModelToolCallDetail(
        **_model_tool_call_summary(model_tool_call).model_dump(),
        input_json=redact_audit_json(model_tool_call.input_json),
        error_message_preview=(
            model_tool_call.error_message[:500]
            if model_tool_call.error_message
            else None
        ),
    )


def _task_evaluation_detail(
    task_evaluation: LlmTaskEvaluation,
) -> TaskEvaluationDetail:
    """Map a Task evaluation to a redacted and bounded internal detail response."""

    return TaskEvaluationDetail(
        **TaskEvaluationSummary.model_validate(task_evaluation).model_dump(),
        findings_json=redact_audit_json(task_evaluation.findings_json),
    )


def _outbox_event_detail(outbox_event: LlmOutboxEvent) -> OutboxEventDetail:
    """Map an outbox fact to a redacted and bounded internal detail response."""

    return OutboxEventDetail(
        **OutboxEventSummary.model_validate(outbox_event).model_dump(),
        payload_json=redact_audit_json(outbox_event.payload_json),
    )


def _decode_query_cursor(
    codec: CursorCodec,
    cursor: str | None,
    query_fingerprint: str,
) -> DatabaseQueryPaginationKey | None:
    """Decode a cursor and reject replay under another internal audit query."""

    after_database_key = codec.decode_query(cursor) if cursor else None
    if after_database_key and after_database_key.query_fingerprint != query_fingerprint:
        raise InvalidCursorError
    return after_database_key


def _encode_query_cursor(
    codec: CursorCodec,
    database_key: DatabaseQueryPaginationKey | None,
) -> str | None:
    """Encode one optional internal database page boundary as a signed cursor."""

    return codec.encode_query(database_key) if database_key else None


def _database_time(database_key: DatabaseQueryPaginationKey) -> datetime:
    """Return a naive UTC timestamp compatible with current database drivers."""

    return database_key.created_at.astimezone(UTC).replace(tzinfo=None)
