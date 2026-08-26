"""Shared dependency aliases used by HTTP controllers."""

from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from nexuspilot_api.core.config import get_settings
from nexuspilot_api.core.pagination import CursorCodec
from nexuspilot_api.infrastructure.database import get_database_session
from nexuspilot_api.infrastructure.object_storage import ObjectStorage, get_object_storage

DatabaseSessionDependency = Annotated[AsyncSession, Depends(get_database_session)]
ObjectStorageDependency = Annotated[ObjectStorage, Depends(get_object_storage)]


def get_cursor_codec() -> CursorCodec:
    """Create the shared signed-cursor codec from server-side configuration."""

    settings = get_settings()
    signing_key = (
        settings.cursor_signing_key.get_secret_value()
        if settings.cursor_signing_key
        else settings.api_key
    )
    return CursorCodec(signing_key)


CursorCodecDependency = Annotated[CursorCodec, Depends(get_cursor_codec)]
