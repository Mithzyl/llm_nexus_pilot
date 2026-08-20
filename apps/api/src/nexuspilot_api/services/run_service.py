"""Transactional run creation and aggregate-query operations."""

from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi import HTTPException, status
from sqlalchemy import and_, desc, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from nexuspilot_api.core.errors import (
    InvalidCursorError,
    InvalidRequestError,
    ResourceConflictError,
    ResourceNotFoundError,
)
from nexuspilot_api.core.pagination import (
    CursorCodec,
    DatabaseQueryPaginationKey,
    database_query_fingerprint,
)
from nexuspilot_api.models import (
    LlmModelAttempt,
    LlmModelTransportAttempt,
    LlmRun,
    LlmRunArtifact,
    LlmSession,
    LlmTask,
    RunStatus,
    SessionStatus,
    TaskStatus,
    User,
)
from nexuspilot_api.models.base import utc_now
from nexuspilot_api.schemas.pagination import CursorPage
from nexuspilot_api.schemas.runs import RunCreate, RunSummary
from nexuspilot_api.services.lookups import require_run
from nexuspilot_api.services.task_service import create_task_control_event

RUN_DETAIL_CHILD_LIMIT = 100


@dataclass(frozen=True)
class RunDatabasePage:
    """Contain one run query page and its next query-bound database key."""

    items: list[LlmRun]
    next_database_key: DatabaseQueryPaginationKey | None


async def create_run(db_session: AsyncSession, payload: RunCreate) -> LlmRun:
    """Persist a new user request after validating that its owner is active."""

    user = await db_session.get(User, payload.user_id)
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Run user must exist and be active",
        )
    if payload.session_id:
        conversation = await db_session.get(LlmSession, payload.session_id)
        if conversation is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Run session must exist",
            )
        if conversation.user_id != payload.user_id:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Run session must belong to the run user",
            )
        if conversation.status != SessionStatus.ACTIVE:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Run session must be active",
            )
    run = LlmRun(**payload.model_dump(), status=RunStatus.PENDING)
    db_session.add(run)
    await db_session.commit()
    await db_session.refresh(run)
    return run


async def get_run_detail(db_session: AsyncSession, run_id: str) -> dict:
    """Load a Run with bounded child snapshots for the legacy aggregate response.

    Each child collection is capped at ``RUN_DETAIL_CHILD_LIMIT`` and reports whether
    additional rows exist. Callers must use the dedicated cursor endpoints to read the
    complete growth history.
    """

    run = await require_run(db_session, run_id)
    tasks = (
        (
            await db_session.execute(
                select(LlmTask)
                .where(LlmTask.run_id == run_id)
                .order_by(LlmTask.created_at, LlmTask.task_id)
                .limit(RUN_DETAIL_CHILD_LIMIT + 1)
            )
        )
        .scalars()
        .all()
    )
    model_attempts = (
        (
            await db_session.execute(
                select(LlmModelAttempt)
                .where(LlmModelAttempt.run_id == run_id)
                .order_by(LlmModelAttempt.started_at, LlmModelAttempt.attempt_id)
                .limit(RUN_DETAIL_CHILD_LIMIT + 1)
            )
        )
        .scalars()
        .all()
    )
    tasks_has_more = len(tasks) > RUN_DETAIL_CHILD_LIMIT
    tasks = tasks[:RUN_DETAIL_CHILD_LIMIT]
    attempts_has_more = len(model_attempts) > RUN_DETAIL_CHILD_LIMIT
    model_attempts = model_attempts[:RUN_DETAIL_CHILD_LIMIT]
    model_attempt_ids = [model_attempt.attempt_id for model_attempt in model_attempts]
    provider_transport_attempts = (
        (
            await db_session.execute(
                select(LlmModelTransportAttempt)
                .where(LlmModelTransportAttempt.attempt_id.in_(model_attempt_ids))
                .order_by(
                    LlmModelTransportAttempt.attempt_id,
                    LlmModelTransportAttempt.attempt_index,
                )
            )
        )
        .scalars()
        .all()
        if model_attempt_ids
        else []
    )
    provider_transport_attempts_by_model_attempt: dict[
        str,
        list[LlmModelTransportAttempt],
    ] = {}
    for provider_transport_attempt in provider_transport_attempts:
        provider_transport_attempts_by_model_attempt.setdefault(
            provider_transport_attempt.attempt_id,
            [],
        ).append(provider_transport_attempt)
    run_artifacts = (
        (
            await db_session.execute(
                select(LlmRunArtifact)
                .where(LlmRunArtifact.run_id == run_id)
                .order_by(LlmRunArtifact.created_at, LlmRunArtifact.artifact_id)
                .limit(RUN_DETAIL_CHILD_LIMIT + 1)
            )
        )
        .scalars()
        .all()
    )
    artifacts_has_more = len(run_artifacts) > RUN_DETAIL_CHILD_LIMIT
    run_artifacts = run_artifacts[:RUN_DETAIL_CHILD_LIMIT]
    model_attempt_details = [
        {
            **model_attempt.__dict__,
            # Provider response identifiers are continuation credentials, not public diagnostics.
            "provider_request_id": None,
            "retries": provider_transport_attempts_by_model_attempt.get(
                model_attempt.attempt_id,
                [],
            ),
        }
        for model_attempt in model_attempts
    ]
    return {
        **run.__dict__,
        "tasks": tasks,
        "attempts": model_attempt_details,
        "artifacts": run_artifacts,
        "tasks_has_more": tasks_has_more,
        "attempts_has_more": attempts_has_more,
        "artifacts_has_more": artifacts_has_more,
    }


async def get_latest_run_detail_for_session(
    db_session: AsyncSession,
    session_id: str,
) -> dict:
    """Return the newest bounded run detail for one session."""

    run = await db_session.scalar(
        select(LlmRun)
        .where(LlmRun.session_id == session_id)
        .order_by(desc(LlmRun.created_at), desc(LlmRun.run_id))
        .limit(1)
    )
    if run is None:
        raise ResourceNotFoundError("Run")
    return await get_run_detail(db_session, run.run_id)


async def list_runs(
    db_session: AsyncSession,
    *,
    codec: CursorCodec,
    cursor: str | None,
    user_id: str | None,
    session_id: str | None,
    run_status: RunStatus | None,
    created_after: datetime | None,
    created_before: datetime | None,
    limit: int,
) -> CursorPage[RunSummary]:
    """Return a bounded run page with a cursor bound to all normalized filters."""

    normalized_after = _normalize_time_filter(created_after, "created_after")
    normalized_before = _normalize_time_filter(created_before, "created_before")
    if normalized_after and normalized_before and normalized_after >= normalized_before:
        raise InvalidRequestError("created_after must be earlier than created_before")
    query_fingerprint = database_query_fingerprint(
        "runs",
        {
            "user_id": user_id,
            "session_id": session_id,
            "status": run_status.value if run_status else None,
            "created_after": normalized_after.isoformat() if normalized_after else None,
            "created_before": normalized_before.isoformat() if normalized_before else None,
        },
    )
    after_database_key = codec.decode_query(cursor) if cursor else None
    if after_database_key and after_database_key.query_fingerprint != query_fingerprint:
        raise InvalidCursorError
    page = await _query_run_database_page(
        db_session,
        user_id=user_id,
        session_id=session_id,
        run_status=run_status,
        created_after=normalized_after,
        created_before=normalized_before,
        after_database_key=after_database_key,
        query_fingerprint=query_fingerprint,
        limit=limit,
    )
    next_cursor = (
        codec.encode_query(page.next_database_key) if page.next_database_key else None
    )
    return CursorPage[RunSummary](
        items=[
            RunSummary(
                run_id=item.run_id,
                user_id=item.user_id,
                session_id=item.session_id,
                request_preview=item.user_request[:200],
                run_type=item.run_type,
                status=item.status,
                budget_limit=item.budget_limit,
                cost_used=item.cost_used,
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


async def cancel_run(db_session: AsyncSession, run_id: str) -> LlmRun:
    """Cancel an active run and atomically request cancellation of running tasks."""

    run = await db_session.scalar(
        select(LlmRun).where(LlmRun.run_id == run_id).with_for_update()
    )
    if run is None:
        raise ResourceNotFoundError("Run")
    if run.status in {RunStatus.CANCEL_REQUESTED, RunStatus.CANCELLED}:
        await db_session.commit()
        return run
    if run.status in {RunStatus.COMPLETED, RunStatus.FAILED}:
        raise ResourceConflictError(f"Cannot cancel run in {run.status.value} status")

    tasks = list(
        (
            await db_session.scalars(
                select(LlmTask)
                .where(LlmTask.run_id == run_id)
                .order_by(LlmTask.task_id)
                .with_for_update()
            )
        ).all()
    )
    cancellation_pending = False
    now = utc_now()
    for task in tasks:
        if task.status == TaskStatus.RUNNING:
            task.status = TaskStatus.CANCEL_REQUESTED
            cancellation_pending = True
            db_session.add(
                create_task_control_event(
                    task,
                    event_type="task.cancel_requested",
                )
            )
        elif task.status == TaskStatus.CANCEL_REQUESTED:
            cancellation_pending = True
        elif task.status not in {
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        }:
            task.status = TaskStatus.CANCELLED
            task.completed_at = now

    if cancellation_pending:
        run.status = RunStatus.CANCEL_REQUESTED
        run.completed_at = None
    else:
        run.status = RunStatus.CANCELLED
        run.completed_at = now
    await db_session.commit()
    await db_session.refresh(run)
    return run


async def _query_run_database_page(
    db_session: AsyncSession,
    *,
    user_id: str | None,
    session_id: str | None,
    run_status: RunStatus | None,
    created_after: datetime | None,
    created_before: datetime | None,
    after_database_key: DatabaseQueryPaginationKey | None,
    query_fingerprint: str,
    limit: int,
) -> RunDatabasePage:
    """Query one ordered run page after an optional query-bound database key."""

    statement = select(LlmRun)
    if user_id is not None:
        statement = statement.where(LlmRun.user_id == user_id)
    if session_id is not None:
        statement = statement.where(LlmRun.session_id == session_id)
    if run_status is not None:
        statement = statement.where(LlmRun.status == run_status)
    if created_after is not None:
        statement = statement.where(LlmRun.created_at >= _database_time(created_after))
    if created_before is not None:
        statement = statement.where(LlmRun.created_at < _database_time(created_before))
    if after_database_key is not None:
        cursor_time = _database_time(after_database_key.created_at)
        statement = statement.where(
            or_(
                LlmRun.created_at > cursor_time,
                and_(
                    LlmRun.created_at == cursor_time,
                    LlmRun.run_id > after_database_key.identifier,
                ),
            )
        )
    statement = statement.order_by(LlmRun.created_at, LlmRun.run_id).limit(limit + 1)
    runs = list((await db_session.scalars(statement)).all())
    has_more = len(runs) > limit
    items = runs[:limit]
    next_database_key = None
    if has_more and items:
        last_run = items[-1]
        next_database_key = DatabaseQueryPaginationKey(
            created_at=last_run.created_at,
            identifier=last_run.run_id,
            query_fingerprint=query_fingerprint,
        )
    return RunDatabasePage(items=items, next_database_key=next_database_key)


def _normalize_time_filter(value: datetime | None, field_name: str) -> datetime | None:
    """Require timezone-aware filters and normalize them to UTC for stable queries."""

    if value is None:
        return None
    if value.tzinfo is None:
        raise InvalidRequestError(f"{field_name} must include a timezone")
    return value.astimezone(UTC)


def _database_time(value: datetime) -> datetime:
    """Convert an aware UTC value to the naive form returned by MySQL and SQLite drivers."""

    return value.astimezone(UTC).replace(tzinfo=None)
