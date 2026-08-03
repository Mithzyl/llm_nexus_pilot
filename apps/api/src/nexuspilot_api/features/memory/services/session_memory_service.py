"""L1 Session State and Summary generation use cases with MinIO snapshot protocol."""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from nexuspilot_api.core.errors import (
    InvalidCursorError,
    InvalidRequestError,
    ResourceConflictError,
    ResourceNotFoundError,
)
from nexuspilot_api.core.pagination import (
    CursorCodec,
    DatabaseSequencePaginationKey,
)
from nexuspilot_api.features.memory.schemas.session_memory import (
    SessionMemoryView,
    SessionStateCreate,
    SessionStateRead,
    SessionSummaryCreate,
    SessionSummaryRead,
)
from nexuspilot_api.features.memory.services.memory_policy import hash_memory_request
from nexuspilot_api.features.memory.services.snapshot_protocol import (
    upload_and_register_snapshot,
)
from nexuspilot_api.infrastructure.object_storage import ObjectStorage
from nexuspilot_api.models import (
    LlmMessage,
    LlmSession,
    LlmSessionState,
    LlmSessionSummary,
    SessionMemoryStatus,
    new_id,
)
from nexuspilot_api.models.base import utc_now
from nexuspilot_api.schemas.pagination import CursorPage


@dataclass(frozen=True)
class SessionMemoryWriteResult:
    """Return one generated Session Memory version and whether it replayed."""

    record: SessionStateRead | SessionSummaryRead
    was_replayed: bool


async def create_session_state(
    db_session: AsyncSession,
    storage: ObjectStorage,
    session_id: str,
    payload: SessionStateCreate,
) -> SessionMemoryWriteResult:
    """Generate one Session State version and atomically switch the current pointer."""

    conversation = await _require_session(db_session, session_id)
    request_hash = hash_memory_request(payload.model_dump(mode="json"))
    existing = await db_session.scalar(
        select(LlmSessionState).where(
            LlmSessionState.session_id == session_id,
            LlmSessionState.idempotency_key == payload.idempotency_key,
        )
    )
    if existing is not None:
        if existing.request_hash != request_hash:
            raise ResourceConflictError(
                "Session state idempotency key was reused with another request"
            )
        return SessionMemoryWriteResult(
            record=SessionStateRead.model_validate(existing),
            was_replayed=True,
        )
    if payload.state_through_message_id:
        await _require_session_message(
            db_session,
            session_id,
            payload.state_through_message_id,
            payload.state_through_message_sequence,
        )
    new_version_number = await _next_session_version(db_session, session_id, LlmSessionState)
    state_id = new_id()
    record = await _switch_versioned_pointer(
        db_session,
        version_class=LlmSessionState,
        parent=conversation,
        parent_scope_column=LlmSessionState.session_id,
        scope_id=session_id,
        pointer_column=LlmSession.current_state_id,
        expected_previous_version=payload.expected_previous_version,
        version_number=new_version_number,
        version_id=state_id,
        idempotency_key=payload.idempotency_key,
        request_hash=request_hash,
        generation_attempt_id=payload.generation_attempt_id,
        status_value=SessionMemoryStatus.ACTIVE,
        storage=storage,
        memory_layer="l1",
        object_type="session_state",
        source_ids=([payload.state_through_message_id] if payload.state_through_message_id else []),
        user_id=conversation.user_id,
        session_id=session_id,
        schema_version=payload.schema_version,
        content=payload.state_json,
        json_column="state_json",
        extra_columns={
            "state_through_message_id": payload.state_through_message_id,
            "state_through_message_sequence": payload.state_through_message_sequence,
        },
    )
    return SessionMemoryWriteResult(
        record=SessionStateRead.model_validate(record),
        was_replayed=False,
    )


async def create_session_summary(
    db_session: AsyncSession,
    storage: ObjectStorage,
    session_id: str,
    payload: SessionSummaryCreate,
) -> SessionMemoryWriteResult:
    """Generate one Session Summary version and atomically switch the current pointer."""

    conversation = await _require_session(db_session, session_id)
    request_hash = hash_memory_request(payload.model_dump(mode="json"))
    existing = await db_session.scalar(
        select(LlmSessionSummary).where(
            LlmSessionSummary.session_id == session_id,
            LlmSessionSummary.idempotency_key == payload.idempotency_key,
        )
    )
    if existing is not None:
        if existing.request_hash != request_hash:
            raise ResourceConflictError(
                "Session summary idempotency key was reused with another request"
            )
        return SessionMemoryWriteResult(
            record=SessionSummaryRead.model_validate(existing),
            was_replayed=True,
        )
    if payload.summary_through_message_id:
        await _require_session_message(
            db_session,
            session_id,
            payload.summary_through_message_id,
            payload.summary_through_message_sequence,
        )
    boundary_message = await db_session.scalar(
        select(LlmMessage).where(
            LlmMessage.session_id == session_id,
            LlmMessage.sequence == payload.summary_from_message_sequence,
        )
    )
    if boundary_message is None:
        raise InvalidRequestError("Summary start sequence has no message in the session")
    new_version_number = await _next_session_version(db_session, session_id, LlmSessionSummary)
    summary_id = new_id()
    record = await _switch_versioned_pointer(
        db_session,
        version_class=LlmSessionSummary,
        parent=conversation,
        parent_scope_column=LlmSessionSummary.session_id,
        scope_id=session_id,
        pointer_column=LlmSession.current_summary_id,
        expected_previous_version=payload.expected_previous_version,
        version_number=new_version_number,
        version_id=summary_id,
        idempotency_key=payload.idempotency_key,
        request_hash=request_hash,
        generation_attempt_id=payload.generation_attempt_id,
        status_value=SessionMemoryStatus.ACTIVE,
        storage=storage,
        memory_layer="l1",
        object_type="session_summary",
        source_ids=(
            [payload.summary_through_message_id]
            if payload.summary_through_message_id
            else [boundary_message.message_id]
        ),
        user_id=conversation.user_id,
        session_id=session_id,
        schema_version=payload.schema_version,
        content=payload.summary_json,
        json_column="summary_json",
        extra_columns={
            "summary_from_message_sequence": payload.summary_from_message_sequence,
            "summary_through_message_id": payload.summary_through_message_id,
            "summary_through_message_sequence": payload.summary_through_message_sequence,
        },
    )
    return SessionMemoryWriteResult(
        record=SessionSummaryRead.model_validate(record),
        was_replayed=False,
    )


async def get_session_memory_view(
    db_session: AsyncSession,
    session_id: str,
) -> SessionMemoryView:
    """Return the current State and Summary versions plus an explicit staleness flag."""

    conversation = await _require_session(db_session, session_id)
    current_state: LlmSessionState | None = None
    current_summary: LlmSessionSummary | None = None
    if conversation.current_state_id:
        current_state = await db_session.get(LlmSessionState, conversation.current_state_id)
        if current_state is None:
            raise ResourceConflictError("Session current state pointer is unavailable")
    if conversation.current_summary_id:
        current_summary = await db_session.get(LlmSessionSummary, conversation.current_summary_id)
        if current_summary is None:
            raise ResourceConflictError("Session current summary pointer is unavailable")
    is_stale = False
    if current_state is not None and current_state.status != SessionMemoryStatus.ACTIVE:
        is_stale = True
    if current_summary is not None and current_summary.status != SessionMemoryStatus.ACTIVE:
        is_stale = True
    return SessionMemoryView(
        session_id=session_id,
        user_id=conversation.user_id,
        current_state=(SessionStateRead.model_validate(current_state) if current_state else None),
        current_summary=(
            SessionSummaryRead.model_validate(current_summary) if current_summary else None
        ),
        is_stale=is_stale,
    )


async def list_session_summaries(
    db_session: AsyncSession,
    session_id: str,
    *,
    codec: CursorCodec,
    cursor: str | None,
    limit: int,
) -> CursorPage[SessionSummaryRead]:
    """Return immutable Session Summary versions in ascending version order."""

    await _require_session(db_session, session_id)
    after_database_key = codec.decode_sequence(cursor) if cursor else None
    if after_database_key and after_database_key.scope_id != session_id:
        raise InvalidCursorError
    statement = select(LlmSessionSummary).where(LlmSessionSummary.session_id == session_id)
    if after_database_key is not None:
        statement = statement.where(LlmSessionSummary.version > after_database_key.sequence)
    summaries = list(
        (
            await db_session.scalars(statement.order_by(LlmSessionSummary.version).limit(limit + 1))
        ).all()
    )
    has_more = len(summaries) > limit
    items = summaries[:limit]
    next_cursor = None
    if has_more and items:
        next_cursor = codec.encode_sequence(
            DatabaseSequencePaginationKey(
                scope_id=session_id,
                sequence=items[-1].version,
            )
        )
    return CursorPage[SessionSummaryRead](
        items=[SessionSummaryRead.model_validate(item) for item in items],
        next_cursor=next_cursor,
        has_more=next_cursor is not None,
        limit=limit,
    )


async def _require_session(db_session: AsyncSession, session_id: str) -> LlmSession:
    """Return one session owned by its immutable owner after validating existence."""

    conversation = await db_session.scalar(
        select(LlmSession).where(LlmSession.session_id == session_id).with_for_update()
    )
    if conversation is None:
        raise ResourceNotFoundError("Session")
    return conversation


async def _require_session_message(
    db_session: AsyncSession,
    session_id: str,
    message_id: str,
    expected_sequence: int | None,
) -> None:
    """Validate that a message belongs to the session with a consistent sequence."""

    message = await db_session.get(LlmMessage, message_id)
    if message is None:
        raise ResourceNotFoundError("Message")
    if message.session_id != session_id:
        raise ResourceConflictError("Message does not belong to the session")
    if expected_sequence is not None and message.sequence != expected_sequence:
        raise ResourceConflictError("Message sequence does not match its recorded range")


async def _next_session_version(
    db_session: AsyncSession,
    session_id: str,
    version_class: type[LlmSessionState] | type[LlmSessionSummary],
) -> int:
    """Return the next monotonic version number for one Session Memory scope."""

    current_version = await db_session.scalar(
        select(version_class.version)
        .where(version_class.session_id == session_id)
        .order_by(version_class.version.desc())
        .limit(1)
    )
    return (current_version or 0) + 1


async def _switch_versioned_pointer(
    db_session: AsyncSession,
    *,
    version_class: type[LlmSessionState] | type[LlmSessionSummary],
    parent: LlmSession,
    parent_scope_column: object,
    scope_id: str,
    pointer_column: object,
    expected_previous_version: int,
    version_number: int,
    version_id: str,
    idempotency_key: str,
    request_hash: str,
    generation_attempt_id: str | None,
    status_value: SessionMemoryStatus,
    storage: ObjectStorage,
    memory_layer: str,
    object_type: str,
    source_ids: list[str],
    user_id: str,
    session_id: str,
    schema_version: str,
    content: dict,
    json_column: str,
    extra_columns: dict,
):
    """Create, upload, register, and activate one Session Memory version atomically."""

    previous_version = await _current_pointer_version(
        db_session,
        version_class=version_class,
        parent=parent,
        pointer_column=pointer_column,
    )
    if expected_previous_version != (previous_version.version if previous_version else 0):
        raise ResourceConflictError(
            "Session Memory version does not match expected_previous_version"
        )
    activated_at = utc_now()
    try:
        registered = await upload_and_register_snapshot(
            db_session=db_session,
            storage=storage,
            user_id=user_id,
            memory_layer=memory_layer,
            scope_id=session_id,
            object_type=object_type,
            schema_version=schema_version,
            version=version_number,
            object_id=version_id,
            content=content,
            source_ids=source_ids,
            session_id=session_id,
            created_at=activated_at,
        )
        if previous_version is not None:
            previous_version.status = SessionMemoryStatus.SUPERSEDED
        previous_identifier = getattr(previous_version, "session_state_id", None) or getattr(
            previous_version, "session_summary_id", None
        )
        version_kwargs: dict = {json_column: content}
        if isinstance(previous_version, LlmSessionState) or version_class is LlmSessionState:
            version_kwargs["session_state_id"] = version_id
            version_kwargs["previous_state_id"] = previous_identifier
        if isinstance(previous_version, LlmSessionSummary) or version_class is LlmSessionSummary:
            version_kwargs["session_summary_id"] = version_id
            version_kwargs["previous_summary_id"] = previous_identifier
        new_version = version_class(
            session_id=scope_id,
            schema_version=schema_version,
            version=version_number,
            status=status_value,
            **version_kwargs,
            generation_attempt_id=generation_attempt_id,
            json_snapshot_object_id=registered.json_object_id,
            markdown_snapshot_object_id=registered.markdown_object_id,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            activated_at=activated_at,
            **extra_columns,
        )
        db_session.add(new_version)
        # The session pointer references this row, so the immutable version must
        # exist before SQLAlchemy emits the parent UPDATE on strict FK engines.
        await db_session.flush()
        setattr(parent, pointer_column.name, version_id)
        await db_session.commit()
        return new_version
    except (IntegrityError, OperationalError) as exc:
        await db_session.rollback()
        replay = await db_session.scalar(
            select(version_class).where(
                parent_scope_column == scope_id,
                version_class.idempotency_key == idempotency_key,
            )
        )
        if replay is not None:
            return replay
        raise ResourceConflictError("Session Memory generation conflicted") from exc


async def _current_pointer_version(
    db_session: AsyncSession,
    *,
    version_class: type[LlmSessionState] | type[LlmSessionSummary],
    parent: LlmSession,
    pointer_column: object,
):
    """Return the currently pointed version row or None for one Session Memory scope."""

    current_id = getattr(parent, pointer_column.name)
    if current_id is None:
        return None
    version = await db_session.get(version_class, current_id)
    if version is None:
        raise ResourceConflictError("Session Memory current pointer is unavailable")
    return version
