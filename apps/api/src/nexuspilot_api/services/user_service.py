"""Transactional user operations."""

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from nexuspilot_api.models import User
from nexuspilot_api.schemas.users import UserCreate


async def create_user(session: AsyncSession, payload: UserCreate) -> User:
    """Create an active user and reject a duplicate stable identifier."""

    if await session.get(User, payload.user_id):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="User already exists")
    user = User(**payload.model_dump())
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user
