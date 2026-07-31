"""Shared dependency aliases used by HTTP controllers."""

from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from nexuspilot_api.infrastructure.database import get_database_session
from nexuspilot_api.infrastructure.object_storage import ObjectStorage, get_object_storage

DatabaseSessionDependency = Annotated[AsyncSession, Depends(get_database_session)]
ObjectStorageDependency = Annotated[ObjectStorage, Depends(get_object_storage)]
