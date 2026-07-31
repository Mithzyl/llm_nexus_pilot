"""Conversation session and immutable message database use cases."""

from dataclasses import dataclass
from datetime import UTC

from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from nexuspilot_api.core.errors import (
    InvalidCursorError,
    ResourceConflictError,
    ResourceNotFoundError,
)
from nexuspilot_api.core.pagination import (
    CursorCodec,
    DatabaseQueryPaginationKey,
    DatabaseSequencePaginationKey,
    database_query_fingerprint,
)
from nexuspilot_api.models import LlmMessage, LlmRun, LlmSession, SessionStatus, User
from nexuspilot_api.schemas.pagination import CursorPage
from nexuspilot_api.schemas.sessions import (
    MessageCreate,
    MessageSummary,
    SessionCreate,
    SessionRead,
    SessionUpdate,
)


@dataclass(frozen=True)
class SessionDatabasePage:
    """Contain one conversation query page and its next database pagination key."""

    items: list[LlmSession]
    next_database_key: DatabaseQueryPaginationKey | None


@dataclass(frozen=True)
class MessageDatabasePage:
    """Contain one message query page and its next sequence pagination key."""

    items: list[LlmMessage]
    next_database_key: DatabaseSequencePaginationKey | None


async def create_session(db_session: AsyncSession, payload: SessionCreate) -> LlmSession:
    """Create a conversation after confirming that its owning user is active."""

    user = await db_session.get(User, payload.user_id)
    if user is None or not user.is_active:
        raise ResourceConflictError("Session user must exist and be active")
    conversation = LlmSession(**payload.model_dump(), status=SessionStatus.ACTIVE)
    db_session.add(conversation)
    await db_session.commit()
    await db_session.refresh(conversation)
    return conversation


async def get_session(db_session: AsyncSession, session_id: str) -> LlmSession:
    """Return one persisted conversation or raise the stable not-found error."""

    conversation = await db_session.get(LlmSession, session_id)
    if conversation is None:
        raise ResourceNotFoundError("Session")
    return conversation


async def list_sessions(
    db_session: AsyncSession,
    *,
    codec: CursorCodec,
    cursor: str | None,
    user_id: str | None,
    session_status: SessionStatus | None,
    limit: int,
) -> CursorPage[SessionRead]:
    """Return a signed-cursor page of conversations filtered by owner or status."""

    query_fingerprint = database_query_fingerprint(
        "sessions",
        {
            "user_id": user_id,
            "status": session_status.value if session_status else None,
        },
    )
    after_database_key = codec.decode_query(cursor) if cursor else None
    if after_database_key and after_database_key.query_fingerprint != query_fingerprint:
        raise InvalidCursorError
    page = await _query_session_database_page(
        db_session,
        user_id=user_id,
        session_status=session_status,
        after_database_key=after_database_key,
        query_fingerprint=query_fingerprint,
        limit=limit,
    )
    next_cursor = (
        codec.encode_query(page.next_database_key) if page.next_database_key else None
    )
    return CursorPage[SessionRead](
        items=[SessionRead.model_validate(item) for item in page.items],
        next_cursor=next_cursor,
        has_more=next_cursor is not None,
        limit=limit,
    )


async def update_session(
    db_session: AsyncSession,
    session_id: str,
    payload: SessionUpdate,
) -> LlmSession:
    """Update declared mutable conversation fields and commit the use case."""

    conversation = await get_session(db_session, session_id)
    for field_name, value in payload.model_dump(exclude_none=True).items():
        setattr(conversation, field_name, value)
    await db_session.commit()
    await db_session.refresh(conversation)
    return conversation


async def create_message(
    db_session: AsyncSession,
    session_id: str,
    payload: MessageCreate,
) -> LlmMessage:
    """Append an immutable message with a database-serialized session sequence."""

    conversation = await db_session.scalar(
        select(LlmSession)
        .where(LlmSession.session_id == session_id)
        .with_for_update()
    )
    if conversation is None:
        raise ResourceNotFoundError("Session")
    if conversation.status != SessionStatus.ACTIVE:
        raise ResourceConflictError("Cannot append a message to an archived session")
    if payload.run_id:
        run = await db_session.get(LlmRun, payload.run_id)
        if run is None:
            raise ResourceNotFoundError("Run")
        if run.user_id != conversation.user_id or run.session_id != session_id:
            raise ResourceConflictError("Run does not belong to session")
    if payload.parent_message_id:
        parent = await db_session.get(LlmMessage, payload.parent_message_id)
        if parent is None:
            raise ResourceNotFoundError("Parent message")
        if parent.session_id != session_id:
            raise ResourceConflictError("Parent message does not belong to session")

    sequence = conversation.next_message_sequence
    conversation.next_message_sequence += 1
    message = LlmMessage(
        **payload.model_dump(),
        session_id=session_id,
        sequence=sequence,
    )
    db_session.add(message)
    try:
        await db_session.commit()
    except IntegrityError as exc:
        await db_session.rollback()
        raise ResourceConflictError("Message sequence conflict") from exc
    await db_session.refresh(message)
    return message


async def get_message(db_session: AsyncSession, message_id: str) -> LlmMessage:
    """Return one immutable message or raise the stable not-found error."""

    message = await db_session.get(LlmMessage, message_id)
    if message is None:
        raise ResourceNotFoundError("Message")
    return message


async def list_messages(
    db_session: AsyncSession,
    session_id: str,
    *,
    codec: CursorCodec,
    cursor: str | None,
    limit: int,
) -> CursorPage[MessageSummary]:
    """Return one session's messages in immutable sequence order."""

    await get_session(db_session, session_id)
    after_database_key = codec.decode_sequence(cursor) if cursor else None
    if after_database_key and after_database_key.scope_id != session_id:
        raise InvalidCursorError
    page = await _query_message_database_page(
        db_session,
        session_id=session_id,
        after_database_key=after_database_key,
        limit=limit,
    )
    next_cursor = (
        codec.encode_sequence(page.next_database_key)
        if page.next_database_key
        else None
    )
    return CursorPage[MessageSummary](
        items=[
            MessageSummary(
                message_id=item.message_id,
                session_id=item.session_id,
                run_id=item.run_id,
                parent_message_id=item.parent_message_id,
                role=item.role,
                content_type=item.content_type,
                content_preview=item.content_text[:200] if item.content_text else None,
                content_uri=item.content_uri,
                sequence=item.sequence,
                token_count=item.token_count,
                created_at=item.created_at,
            )
            for item in page.items
        ],
        next_cursor=next_cursor,
        has_more=next_cursor is not None,
        limit=limit,
    )


async def _query_session_database_page(
    db_session: AsyncSession,
    *,
    user_id: str | None,
    session_status: SessionStatus | None,
    after_database_key: DatabaseQueryPaginationKey | None,
    query_fingerprint: str,
    limit: int,
) -> SessionDatabasePage:
    """Query one stable database page of conversations after an optional key."""

    statement = select(LlmSession)
    if user_id is not None:
        statement = statement.where(LlmSession.user_id == user_id)
    if session_status is not None:
        statement = statement.where(LlmSession.status == session_status)
    if after_database_key is not None:
        cursor_time = after_database_key.created_at.astimezone(UTC).replace(tzinfo=None)
        statement = statement.where(
            or_(
                LlmSession.created_at > cursor_time,
                and_(
                    LlmSession.created_at == cursor_time,
                    LlmSession.session_id > after_database_key.identifier,
                ),
            )
        )
    statement = statement.order_by(LlmSession.created_at, LlmSession.session_id).limit(limit + 1)
    conversations = list((await db_session.scalars(statement)).all())
    has_more = len(conversations) > limit
    items = conversations[:limit]
    next_database_key = None
    if has_more and items:
        last_session = items[-1]
        next_database_key = DatabaseQueryPaginationKey(
            created_at=last_session.created_at,
            identifier=last_session.session_id,
            query_fingerprint=query_fingerprint,
        )
    return SessionDatabasePage(items=items, next_database_key=next_database_key)


async def _query_message_database_page(
    db_session: AsyncSession,
    *,
    session_id: str,
    after_database_key: DatabaseSequencePaginationKey | None,
    limit: int,
) -> MessageDatabasePage:
    """Query one stable database page of messages after an optional sequence key."""

    statement = select(LlmMessage).where(LlmMessage.session_id == session_id)
    if after_database_key is not None:
        statement = statement.where(LlmMessage.sequence > after_database_key.sequence)
    statement = statement.order_by(LlmMessage.sequence).limit(limit + 1)
    messages = list((await db_session.scalars(statement)).all())
    has_more = len(messages) > limit
    items = messages[:limit]
    next_database_key = None
    if has_more and items:
        next_database_key = DatabaseSequencePaginationKey(
            scope_id=session_id,
            sequence=items[-1].sequence,
        )
    return MessageDatabasePage(items=items, next_database_key=next_database_key)
