"""Trusted user administration controller."""

from fastapi import APIRouter, Query, status

from nexuspilot_api.routers.common import CursorCodecDependency, DatabaseSessionDependency
from nexuspilot_api.schemas.pagination import CursorPage
from nexuspilot_api.schemas.users import UserCreate, UserRead, UserUpdate
from nexuspilot_api.services.user_service import (
    create_user,
    get_user,
    list_users,
    update_user,
)

router = APIRouter(tags=["users"])


@router.post("/users", response_model=UserRead, status_code=status.HTTP_201_CREATED)
async def post_user(payload: UserCreate, db_session: DatabaseSessionDependency) -> UserRead:
    """Create a basic active platform identity for subsequent run ownership."""

    return UserRead.model_validate(await create_user(db_session, payload))


@router.get("/users", response_model=CursorPage[UserRead])
async def get_users(
    db_session: DatabaseSessionDependency,
    cursor_codec: CursorCodecDependency,
    cursor: str | None = None,
    is_active: bool | None = None,
    limit: int = Query(default=50, ge=1, le=100),
) -> CursorPage[UserRead]:
    """List users using stable signed cursor pagination and optional status filtering."""

    return await list_users(
        db_session,
        codec=cursor_codec,
        cursor=cursor,
        is_active=is_active,
        limit=limit,
    )


@router.get("/users/{user_id}", response_model=UserRead)
async def get_user_by_id(user_id: str, db_session: DatabaseSessionDependency) -> UserRead:
    """Return one user profile to a trusted API caller."""

    return UserRead.model_validate(await get_user(db_session, user_id))


@router.patch("/users/{user_id}", response_model=UserRead)
async def patch_user(
    user_id: str,
    payload: UserUpdate,
    db_session: DatabaseSessionDependency,
) -> UserRead:
    """Update the user's display name or active state without allowing ownership changes."""

    return UserRead.model_validate(await update_user(db_session, user_id, payload))
