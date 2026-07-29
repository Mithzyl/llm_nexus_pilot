"""User resource controller."""

from fastapi import APIRouter, status

from nexuspilot_api.routers.common import SessionDependency
from nexuspilot_api.schemas.users import UserCreate, UserRead
from nexuspilot_api.services.user_service import create_user

router = APIRouter(tags=["users"])


@router.post("/users", response_model=UserRead, status_code=status.HTTP_201_CREATED)
async def post_user(payload: UserCreate, session: SessionDependency) -> UserRead:
    """Create a basic active platform identity for subsequent run ownership."""

    return UserRead.model_validate(await create_user(session, payload))
