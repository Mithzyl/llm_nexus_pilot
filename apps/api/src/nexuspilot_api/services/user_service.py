"""User use cases and their database transaction boundaries."""

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
    database_query_fingerprint,
)
from nexuspilot_api.models import User
from nexuspilot_api.schemas.pagination import CursorPage
from nexuspilot_api.schemas.users import UserCreate, UserRead, UserUpdate


@dataclass(frozen=True)
class UserDatabasePage:
    """Contain one user query page and its next database pagination key."""

    items: list[User]
    next_database_key: DatabaseQueryPaginationKey | None


async def create_user(db_session: AsyncSession, payload: UserCreate) -> User:
    """Create and commit an active user through the request database session."""

    if await db_session.get(User, payload.user_id):
        raise ResourceConflictError("User already exists")
    user = User(**payload.model_dump())
    db_session.add(user)
    try:
        await db_session.commit()
    except IntegrityError as exc:
        await db_session.rollback()
        raise ResourceConflictError("User already exists") from exc
    await db_session.refresh(user)
    return user


async def get_user(db_session: AsyncSession, user_id: str) -> User:
    """Return one user or raise a stable application not-found error."""

    user = await db_session.get(User, user_id)
    if user is None:
        raise ResourceNotFoundError("User")
    return user


async def list_users(
    db_session: AsyncSession,
    *,
    codec: CursorCodec,
    cursor: str | None,
    is_active: bool | None,
    limit: int,
) -> CursorPage[UserRead]:
    """Return a stable signed-cursor page of optionally filtered users."""

    query_fingerprint = database_query_fingerprint(
        "users",
        {"is_active": is_active},
    )
    after_database_key = codec.decode_query(cursor) if cursor else None
    if after_database_key and after_database_key.query_fingerprint != query_fingerprint:
        raise InvalidCursorError
    page = await _query_user_database_page(
        db_session,
        is_active=is_active,
        after_database_key=after_database_key,
        query_fingerprint=query_fingerprint,
        limit=limit,
    )
    next_cursor = (
        codec.encode_query(page.next_database_key) if page.next_database_key else None
    )
    return CursorPage[UserRead](
        items=[UserRead.model_validate(user) for user in page.items],
        next_cursor=next_cursor,
        has_more=next_cursor is not None,
        limit=limit,
    )


async def update_user(
    db_session: AsyncSession,
    user_id: str,
    payload: UserUpdate,
) -> User:
    """Update only explicit mutable fields and commit them as one use case."""

    user = await get_user(db_session, user_id)
    for field_name, value in payload.model_dump(exclude_none=True).items():
        setattr(user, field_name, value)
    await db_session.commit()
    await db_session.refresh(user)
    return user


async def _query_user_database_page(
    db_session: AsyncSession,
    *,
    is_active: bool | None,
    after_database_key: DatabaseQueryPaginationKey | None,
    query_fingerprint: str,
    limit: int,
) -> UserDatabasePage:
    """Query one ordered user page after an optional database pagination key."""

    statement = select(User)
    if is_active is not None:
        statement = statement.where(User.is_active == is_active)
    if after_database_key is not None:
        cursor_time = after_database_key.created_at.astimezone(UTC).replace(tzinfo=None)
        statement = statement.where(
            or_(
                User.created_at > cursor_time,
                and_(
                    User.created_at == cursor_time,
                    User.user_id > after_database_key.identifier,
                ),
            )
        )
    statement = statement.order_by(User.created_at, User.user_id).limit(limit + 1)
    users = list((await db_session.scalars(statement)).all())
    has_more = len(users) > limit
    items = users[:limit]
    next_database_key = None
    if has_more and items:
        last_user = items[-1]
        next_database_key = DatabaseQueryPaginationKey(
            created_at=last_user.created_at,
            identifier=last_user.user_id,
            query_fingerprint=query_fingerprint,
        )
    return UserDatabasePage(items=items, next_database_key=next_database_key)
