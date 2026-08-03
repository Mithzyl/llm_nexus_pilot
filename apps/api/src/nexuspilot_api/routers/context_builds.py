"""Context Build preview and evidence controllers."""

from fastapi import APIRouter, status

from nexuspilot_api.routers.common import DatabaseSessionDependency
from nexuspilot_api.schemas.context_builds import (
    ContextBuildCreate,
    ContextBuildRead,
)
from nexuspilot_api.services.context_builder_service import (
    build_context,
    get_context_build,
)

router = APIRouter(tags=["context-builds"])


@router.post(
    "/context-builds/preview",
    response_model=ContextBuildRead,
    status_code=status.HTTP_201_CREATED,
)
async def post_context_build_preview(
    payload: ContextBuildCreate,
    db_session: DatabaseSessionDependency,
) -> ContextBuildRead:
    """Assemble one version-pinned Context Build without invoking any model."""

    return await build_context(db_session, payload)


@router.get(
    "/context-builds/{context_build_id}",
    response_model=ContextBuildRead,
)
async def get_context_build_by_id(
    context_build_id: str,
    db_session: DatabaseSessionDependency,
) -> ContextBuildRead:
    """Return one persisted Context Build with historical selection evidence."""

    return await get_context_build(db_session, context_build_id)
