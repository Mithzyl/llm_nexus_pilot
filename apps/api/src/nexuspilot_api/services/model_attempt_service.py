"""Transactional creation and bounded queries for LLM model invocations."""

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import and_, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from nexuspilot_api.core.errors import (
    InvalidCursorError,
    InvalidRequestError,
    ResourceNotFoundError,
)
from nexuspilot_api.core.pagination import (
    CursorCodec,
    DatabaseQueryPaginationKey,
    DatabaseSequencePaginationKey,
    database_query_fingerprint,
)
from nexuspilot_api.models import (
    AttemptStatus,
    LlmContextBuild,
    LlmModelAttempt,
    LlmModelTransportAttempt,
    LlmRun,
)
from nexuspilot_api.schemas.model_attempts import (
    ModelAttemptCreate,
    ModelAttemptDetail,
    ModelAttemptSummary,
    ModelTransportAttemptDetail,
    ModelTransportAttemptSummary,
)
from nexuspilot_api.schemas.pagination import CursorPage
from nexuspilot_api.services.lookups import require_run, require_task


@dataclass(frozen=True)
class ModelAttemptDatabasePage:
    """Contain one model invocation page and its next query-bound database key."""

    items: list[LlmModelAttempt]
    next_database_key: DatabaseQueryPaginationKey | None


@dataclass(frozen=True)
class ModelTransportAttemptDatabasePage:
    """Contain provider transport attempts and the next model-scoped sequence key."""

    items: list[LlmModelTransportAttempt]
    next_database_key: DatabaseSequencePaginationKey | None


async def create_model_attempt(
    db_session: AsyncSession,
    run_id: str,
    payload: ModelAttemptCreate,
) -> LlmModelAttempt:
    """Save one model call and add its estimated cost to the owning run atomically."""

    await require_run(db_session, run_id)
    if payload.task_id:
        task = await require_task(db_session, payload.task_id)
        if task.run_id != run_id:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Task does not belong to the run",
            )
    if payload.context_build_id:
        context_build = await db_session.get(LlmContextBuild, payload.context_build_id)
        if context_build is None:
            raise ResourceNotFoundError("Context Build")
        if context_build.run_id != run_id:
            raise InvalidRequestError("Context Build does not belong to the run")
    model_attempt = LlmModelAttempt(run_id=run_id, **payload.model_dump())
    db_session.add(model_attempt)
    cost = payload.estimated_cost or Decimal("0")
    await db_session.execute(
        update(LlmRun).where(LlmRun.run_id == run_id).values(cost_used=LlmRun.cost_used + cost)
    )
    await db_session.commit()
    await db_session.refresh(model_attempt)
    return model_attempt


async def get_model_attempt(
    db_session: AsyncSession,
    attempt_id: str,
) -> ModelAttemptDetail:
    """Return safe detail for one logical model invocation or raise not found."""

    model_attempt = await db_session.get(LlmModelAttempt, attempt_id)
    if model_attempt is None:
        raise ResourceNotFoundError("Attempt")
    return _model_attempt_detail(model_attempt)


async def list_model_attempts(
    db_session: AsyncSession,
    *,
    codec: CursorCodec,
    cursor: str | None,
    run_id: str | None,
    task_id: str | None,
    provider: str | None,
    model: str | None,
    attempt_status: AttemptStatus | None,
    started_after: datetime | None,
    started_before: datetime | None,
    limit: int,
) -> CursorPage[ModelAttemptSummary]:
    """Return an owner-scoped model invocation page with query-bound pagination."""

    if run_id is None and task_id is None:
        raise InvalidRequestError("run_id or task_id is required")
    if run_id is not None:
        await require_run(db_session, run_id)
    if task_id is not None:
        task = await require_task(db_session, task_id)
        if run_id is not None and task.run_id != run_id:
            raise InvalidRequestError("Task does not belong to the run")

    normalized_after = _normalize_time_filter(started_after, "started_after")
    normalized_before = _normalize_time_filter(started_before, "started_before")
    if normalized_after and normalized_before and normalized_after >= normalized_before:
        raise InvalidRequestError("started_after must be earlier than started_before")
    query_fingerprint = database_query_fingerprint(
        "attempts",
        {
            "run_id": run_id,
            "task_id": task_id,
            "provider": provider,
            "model": model,
            "status": attempt_status.value if attempt_status else None,
            "started_after": normalized_after.isoformat() if normalized_after else None,
            "started_before": normalized_before.isoformat() if normalized_before else None,
        },
    )
    after_database_key = codec.decode_query(cursor) if cursor else None
    if after_database_key and after_database_key.query_fingerprint != query_fingerprint:
        raise InvalidCursorError
    database_page = await _query_model_attempt_database_page(
        db_session,
        run_id=run_id,
        task_id=task_id,
        provider=provider,
        model=model,
        attempt_status=attempt_status,
        started_after=normalized_after,
        started_before=normalized_before,
        after_database_key=after_database_key,
        query_fingerprint=query_fingerprint,
        limit=limit,
    )
    next_cursor = (
        codec.encode_query(database_page.next_database_key)
        if database_page.next_database_key
        else None
    )
    return CursorPage[ModelAttemptSummary](
        items=[
            ModelAttemptSummary.model_validate(model_attempt)
            for model_attempt in database_page.items
        ],
        next_cursor=next_cursor,
        has_more=next_cursor is not None,
        limit=limit,
    )


async def get_model_transport_attempt(
    db_session: AsyncSession,
    retry_id: str,
) -> ModelTransportAttemptDetail:
    """Return safe detail for one physical provider request or raise not found."""

    model_transport_attempt = await db_session.get(LlmModelTransportAttempt, retry_id)
    if model_transport_attempt is None:
        raise ResourceNotFoundError("Model transport attempt")
    return _model_transport_attempt_detail(model_transport_attempt)


async def list_model_transport_attempts(
    db_session: AsyncSession,
    attempt_id: str,
    *,
    codec: CursorCodec,
    cursor: str | None,
    limit: int,
) -> CursorPage[ModelTransportAttemptSummary]:
    """Return physical requests in stable sequence order under one logical Attempt."""

    if await db_session.get(LlmModelAttempt, attempt_id) is None:
        raise ResourceNotFoundError("Attempt")
    after_database_key = codec.decode_sequence(cursor) if cursor else None
    if after_database_key and after_database_key.scope_id != attempt_id:
        raise InvalidCursorError
    database_page = await _query_model_transport_attempt_database_page(
        db_session,
        attempt_id=attempt_id,
        after_database_key=after_database_key,
        limit=limit,
    )
    next_cursor = (
        codec.encode_sequence(database_page.next_database_key)
        if database_page.next_database_key
        else None
    )
    return CursorPage[ModelTransportAttemptSummary](
        items=[
            _model_transport_attempt_summary(model_transport_attempt)
            for model_transport_attempt in database_page.items
        ],
        next_cursor=next_cursor,
        has_more=next_cursor is not None,
        limit=limit,
    )


async def _query_model_attempt_database_page(
    db_session: AsyncSession,
    *,
    run_id: str | None,
    task_id: str | None,
    provider: str | None,
    model: str | None,
    attempt_status: AttemptStatus | None,
    started_after: datetime | None,
    started_before: datetime | None,
    after_database_key: DatabaseQueryPaginationKey | None,
    query_fingerprint: str,
    limit: int,
) -> ModelAttemptDatabasePage:
    """Query one ordered model invocation page after an optional query-bound key."""

    statement = select(LlmModelAttempt)
    if run_id is not None:
        statement = statement.where(LlmModelAttempt.run_id == run_id)
    if task_id is not None:
        statement = statement.where(LlmModelAttempt.task_id == task_id)
    if provider is not None:
        statement = statement.where(LlmModelAttempt.provider == provider)
    if model is not None:
        statement = statement.where(LlmModelAttempt.model == model)
    if attempt_status is not None:
        statement = statement.where(LlmModelAttempt.status == attempt_status)
    if started_after is not None:
        statement = statement.where(
            LlmModelAttempt.started_at >= _database_time(started_after)
        )
    if started_before is not None:
        statement = statement.where(
            LlmModelAttempt.started_at < _database_time(started_before)
        )
    if after_database_key is not None:
        cursor_time = _database_time(after_database_key.created_at)
        statement = statement.where(
            or_(
                LlmModelAttempt.started_at > cursor_time,
                and_(
                    LlmModelAttempt.started_at == cursor_time,
                    LlmModelAttempt.attempt_id > after_database_key.identifier,
                ),
            )
        )
    statement = statement.order_by(
        LlmModelAttempt.started_at,
        LlmModelAttempt.attempt_id,
    ).limit(limit + 1)
    model_attempts = list((await db_session.scalars(statement)).all())
    has_more = len(model_attempts) > limit
    items = model_attempts[:limit]
    next_database_key = None
    if has_more and items:
        last_model_attempt = items[-1]
        next_database_key = DatabaseQueryPaginationKey(
            created_at=last_model_attempt.started_at,
            identifier=last_model_attempt.attempt_id,
            query_fingerprint=query_fingerprint,
        )
    return ModelAttemptDatabasePage(items=items, next_database_key=next_database_key)


async def _query_model_transport_attempt_database_page(
    db_session: AsyncSession,
    *,
    attempt_id: str,
    after_database_key: DatabaseSequencePaginationKey | None,
    limit: int,
) -> ModelTransportAttemptDatabasePage:
    """Query provider transport attempts after an optional model-scoped sequence key."""

    statement = select(LlmModelTransportAttempt).where(
        LlmModelTransportAttempt.attempt_id == attempt_id
    )
    if after_database_key is not None:
        statement = statement.where(
            LlmModelTransportAttempt.attempt_index > after_database_key.sequence
        )
    statement = statement.order_by(LlmModelTransportAttempt.attempt_index).limit(limit + 1)
    model_transport_attempts = list((await db_session.scalars(statement)).all())
    has_more = len(model_transport_attempts) > limit
    items = model_transport_attempts[:limit]
    next_database_key = None
    if has_more and items:
        next_database_key = DatabaseSequencePaginationKey(
            scope_id=attempt_id,
            sequence=items[-1].attempt_index,
        )
    return ModelTransportAttemptDatabasePage(
        items=items,
        next_database_key=next_database_key,
    )


def _model_attempt_detail(model_attempt: LlmModelAttempt) -> ModelAttemptDetail:
    """Map internal Attempt storage fields to a safe public detail contract."""

    return ModelAttemptDetail(
        **ModelAttemptSummary.model_validate(model_attempt).model_dump(),
        request_key=model_attempt.request_key,
        # Provider response identifiers can authorize native continuation and stay backend-only.
        provider_request_id=None,
        error_message_preview=_error_preview(model_attempt.error_message),
        has_raw_request=model_attempt.raw_request_uri is not None,
        has_raw_response=model_attempt.raw_response_uri is not None,
    )


def _model_transport_attempt_summary(
    model_transport_attempt: LlmModelTransportAttempt,
) -> ModelTransportAttemptSummary:
    """Map a physical provider request to its bounded public summary."""

    return ModelTransportAttemptSummary(
        retry_id=model_transport_attempt.retry_id,
        attempt_id=model_transport_attempt.attempt_id,
        attempt_index=model_transport_attempt.attempt_index,
        status_code=model_transport_attempt.status_code,
        latency_ms=model_transport_attempt.latency_ms,
        error_type=model_transport_attempt.error_type,
        error_message_preview=_error_preview(model_transport_attempt.error_message),
        created_at=model_transport_attempt.created_at,
    )


def _model_transport_attempt_detail(
    model_transport_attempt: LlmModelTransportAttempt,
) -> ModelTransportAttemptDetail:
    """Map a physical provider request to its complete safe public detail."""

    return ModelTransportAttemptDetail(
        **_model_transport_attempt_summary(model_transport_attempt).model_dump()
    )


def _error_preview(error_message: str | None) -> str | None:
    """Bound stored provider error text before exposing it through query endpoints."""

    return error_message[:500] if error_message else None


def _normalize_time_filter(value: datetime | None, field_name: str) -> datetime | None:
    """Require timezone-aware model invocation filters and normalize them to UTC."""

    if value is None:
        return None
    if value.tzinfo is None:
        raise InvalidRequestError(f"{field_name} must include a timezone")
    return value.astimezone(UTC)


def _database_time(value: datetime) -> datetime:
    """Convert an aware UTC value to the naive form used by current database drivers."""

    return value.astimezone(UTC).replace(tzinfo=None)
