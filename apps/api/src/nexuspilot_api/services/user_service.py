"""Transactional user operations."""

from sqlalchemy.exc import IntegrityError

from nexuspilot_api.core.errors import ResourceConflictError, ResourceNotFoundError
from nexuspilot_api.core.pagination import CursorCodec
from nexuspilot_api.infrastructure.unit_of_work import SqlAlchemyUnitOfWork
from nexuspilot_api.models import User
from nexuspilot_api.schemas.pagination import CursorPage
from nexuspilot_api.schemas.users import UserCreate, UserRead, UserUpdate


async def create_user(uow: SqlAlchemyUnitOfWork, payload: UserCreate) -> User:
    """Create an active user and commit the use case through one Unit of Work."""

    if await uow.users.get(payload.user_id):
        raise ResourceConflictError("User already exists")
    user = User(**payload.model_dump())
    uow.users.add(user)
    try:
        await uow.commit()
    except IntegrityError as exc:
        await uow.rollback()
        raise ResourceConflictError("User already exists") from exc
    await uow.refresh(user)
    return user


async def get_user(uow: SqlAlchemyUnitOfWork, user_id: str) -> User:
    """Return one user or raise a stable application not-found error."""

    user = await uow.users.get(user_id)
    if user is None:
        raise ResourceNotFoundError("User")
    return user


async def list_users(
    uow: SqlAlchemyUnitOfWork,
    *,
    codec: CursorCodec,
    cursor: str | None,
    is_active: bool | None,
    limit: int,
) -> CursorPage[UserRead]:
    """Return a stable signed-cursor page of optionally filtered users."""

    position = codec.decode(cursor) if cursor else None
    page = await uow.users.list_page(
        is_active=is_active,
        position=position,
        limit=limit,
    )
    next_cursor = codec.encode(page.next_position) if page.next_position else None
    return CursorPage[UserRead](
        items=[UserRead.model_validate(user) for user in page.items],
        next_cursor=next_cursor,
        has_more=next_cursor is not None,
        limit=limit,
    )


async def update_user(
    uow: SqlAlchemyUnitOfWork,
    user_id: str,
    payload: UserUpdate,
) -> User:
    """Update only explicit mutable fields and commit them as one use case."""

    user = await get_user(uow, user_id)
    for field_name, value in payload.model_dump(exclude_none=True).items():
        setattr(user, field_name, value)
    await uow.commit()
    await uow.refresh(user)
    return user
