"""Shared dependency aliases used by HTTP controllers."""

from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from nexuspilot_api.infrastructure.database import get_session
from nexuspilot_api.infrastructure.object_storage import ObjectStorage, get_object_storage
from nexuspilot_api.infrastructure.unit_of_work import SqlAlchemyUnitOfWork

SessionDependency = Annotated[AsyncSession, Depends(get_session)]
ObjectStorageDependency = Annotated[ObjectStorage, Depends(get_object_storage)]


def get_unit_of_work(session: SessionDependency) -> SqlAlchemyUnitOfWork:
    """Create one Unit of Work around the request-scoped SQLAlchemy session."""

    return SqlAlchemyUnitOfWork(session)


UnitOfWorkDependency = Annotated[SqlAlchemyUnitOfWork, Depends(get_unit_of_work)]
