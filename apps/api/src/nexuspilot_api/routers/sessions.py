"""Conversation session and immutable message controllers."""

from typing import Annotated

from fastapi import APIRouter, Query, status

from nexuspilot_api.routers.common import CursorCodecDependency, DatabaseSessionDependency
from nexuspilot_api.schemas.pagination import CursorPage
from nexuspilot_api.schemas.runs import RunDetail
from nexuspilot_api.schemas.sessions import (
    MessageCreate,
    MessageRead,
    MessageSummary,
    SessionCreate,
    SessionRead,
    SessionStatus,
    SessionUpdate,
)
from nexuspilot_api.services.run_service import get_latest_run_detail_for_session
from nexuspilot_api.services.session_service import (
    create_message,
    create_session,
    get_message,
    get_session,
    list_latest_message_details,
    list_message_details,
    list_messages,
    list_sessions,
    update_session,
)

router = APIRouter(tags=["sessions"])


@router.post("/sessions", response_model=SessionRead, status_code=status.HTTP_201_CREATED)
async def post_session(
    payload: SessionCreate,
    db_session: DatabaseSessionDependency,
) -> SessionRead:
    """Create a durable conversation for an active platform user."""

    return SessionRead.model_validate(await create_session(db_session, payload))


@router.get("/sessions", response_model=CursorPage[SessionRead])
async def get_sessions(
    db_session: DatabaseSessionDependency,
    cursor_codec: CursorCodecDependency,
    cursor: str | None = None,
    user_id: str | None = None,
    session_status: Annotated[SessionStatus | None, Query(alias="status")] = None,
    limit: int = Query(default=50, ge=1, le=100),
) -> CursorPage[SessionRead]:
    """List conversations using stable cursor pagination and optional filters."""

    return await list_sessions(
        db_session,
        codec=cursor_codec,
        cursor=cursor,
        user_id=user_id,
        session_status=session_status,
        limit=limit,
    )


@router.get("/sessions/{session_id}", response_model=SessionRead)
async def get_session_by_id(
    session_id: str,
    db_session: DatabaseSessionDependency,
) -> SessionRead:
    """Return one durable conversation and its current lifecycle state."""

    return SessionRead.model_validate(await get_session(db_session, session_id))


@router.get("/sessions/{session_id}/latest-run", response_model=RunDetail)
async def get_latest_session_run(
    session_id: str,
    db_session: DatabaseSessionDependency,
) -> RunDetail:
    """Return the newest run detail used to restore the session inspector."""

    await get_session(db_session, session_id)
    return RunDetail.model_validate(
        await get_latest_run_detail_for_session(db_session, session_id),
    )


@router.patch("/sessions/{session_id}", response_model=SessionRead)
async def patch_session(
    session_id: str,
    payload: SessionUpdate,
    db_session: DatabaseSessionDependency,
) -> SessionRead:
    """Update only the conversation title or lifecycle status."""

    return SessionRead.model_validate(await update_session(db_session, session_id, payload))


@router.post(
    "/sessions/{session_id}/messages",
    response_model=MessageRead,
    status_code=status.HTTP_201_CREATED,
)
async def post_message(
    session_id: str,
    payload: MessageCreate,
    db_session: DatabaseSessionDependency,
) -> MessageRead:
    """Append one immutable message to an active conversation."""

    return MessageRead.model_validate(await create_message(db_session, session_id, payload))


@router.get("/sessions/{session_id}/messages", response_model=CursorPage[MessageSummary])
async def get_session_messages(
    session_id: str,
    db_session: DatabaseSessionDependency,
    cursor_codec: CursorCodecDependency,
    cursor: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
) -> CursorPage[MessageSummary]:
    """List immutable messages in their database-assigned sequence order."""

    return await list_messages(
        db_session,
        session_id,
        codec=cursor_codec,
        cursor=cursor,
        limit=limit,
    )


@router.get(
    "/sessions/{session_id}/messages/full",
    response_model=CursorPage[MessageRead],
)
async def get_session_message_details(
    session_id: str,
    db_session: DatabaseSessionDependency,
    cursor_codec: CursorCodecDependency,
    cursor: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
) -> CursorPage[MessageRead]:
    """Return one bounded page of complete message bodies in sequence order."""

    return await list_message_details(
        db_session,
        session_id,
        codec=cursor_codec,
        cursor=cursor,
        limit=limit,
    )


@router.get(
    "/sessions/{session_id}/messages/latest",
    response_model=CursorPage[MessageRead],
)
async def get_latest_session_message_details(
    session_id: str,
    db_session: DatabaseSessionDependency,
    cursor_codec: CursorCodecDependency,
    cursor: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
) -> CursorPage[MessageRead]:
    """Return the newest complete message window and cursor older messages."""

    return await list_latest_message_details(
        db_session,
        session_id,
        codec=cursor_codec,
        cursor=cursor,
        limit=limit,
    )


@router.get("/messages/{message_id}", response_model=MessageRead)
async def get_message_by_id(
    message_id: str,
    db_session: DatabaseSessionDependency,
) -> MessageRead:
    """Return one immutable conversation message by identifier."""

    return MessageRead.model_validate(await get_message(db_session, message_id))
