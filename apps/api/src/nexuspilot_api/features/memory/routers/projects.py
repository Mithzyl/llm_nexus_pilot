"""Project scope, Memory policy, workspace, and Project Memory controllers."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status

from nexuspilot_api.core.security import require_internal_api_key
from nexuspilot_api.features.memory.schemas.memories import MemoryRead
from nexuspilot_api.features.memory.schemas.projects import (
    MemoryStatus,
    ProjectCreate,
    ProjectMemoryCandidateRead,
    ProjectMemoryPolicyUpdate,
    ProjectMemoryProfileRead,
    ProjectProfileRebuildCreate,
    ProjectRead,
    ProjectStatus,
    ProjectUpdate,
    ProjectWorkspaceCreate,
    ProjectWorkspaceRead,
)
from nexuspilot_api.features.memory.schemas.user_memory import CandidateDecisionCreate
from nexuspilot_api.features.memory.services.profile_snapshot_service import (
    get_project_profile,
    rebuild_project_profile,
)
from nexuspilot_api.features.memory.services.project_memory_service import (
    approve_project_memory_candidate,
    list_project_memory_candidates,
)
from nexuspilot_api.features.memory.services.project_service import (
    add_project_workspace,
    create_project,
    get_project,
    list_projects,
    update_memory_policy,
    update_project,
)
from nexuspilot_api.routers.common import (
    CursorCodecDependency,
    DatabaseSessionDependency,
    ObjectStorageDependency,
)
from nexuspilot_api.schemas.pagination import CursorPage

router = APIRouter(tags=["projects"])
internal_key = [Depends(require_internal_api_key)]


@router.get("/projects", response_model=CursorPage[ProjectRead])
async def get_projects(
    db_session: DatabaseSessionDependency,
    cursor_codec: CursorCodecDependency,
    cursor: Annotated[str | None, Query(max_length=2048)] = None,
    owner_user_id: Annotated[str | None, Query(min_length=1, max_length=128)] = None,
    project_status: Annotated[ProjectStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> CursorPage[ProjectRead]:
    """Return a filter-bound page of minimal Project scopes."""

    return await list_projects(
        db_session,
        codec=cursor_codec,
        cursor=cursor,
        owner_user_id=owner_user_id,
        project_status=project_status,
        limit=limit,
    )


@router.get("/projects/{project_id}", response_model=ProjectRead)
async def get_project_by_id(
    project_id: str,
    db_session: DatabaseSessionDependency,
) -> ProjectRead:
    """Return one minimal Project scope."""

    return await get_project(db_session, project_id)


@router.get(
    "/projects/{project_id}/memory",
    response_model=ProjectMemoryProfileRead,
)
async def get_project_memory(
    project_id: str,
    db_session: DatabaseSessionDependency,
) -> ProjectMemoryProfileRead:
    """Return the current Project core Profile with token evidence."""

    return await get_project_profile(db_session, project_id)


@router.get(
    "/projects/{project_id}/memory-candidates",
    response_model=CursorPage[ProjectMemoryCandidateRead],
)
async def get_project_memory_candidates(
    project_id: str,
    db_session: DatabaseSessionDependency,
    cursor_codec: CursorCodecDependency,
    cursor: Annotated[str | None, Query(max_length=2048)] = None,
    memory_status: Annotated[MemoryStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> CursorPage[ProjectMemoryCandidateRead]:
    """Page Project Memory candidates and formal facts with filter-bound cursors."""

    return await list_project_memory_candidates(
        db_session,
        project_id,
        codec=cursor_codec,
        cursor=cursor,
        status=memory_status,
        limit=limit,
    )


@router.post(
    "/projects",
    response_model=ProjectRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=internal_key,
)
async def post_project(
    payload: ProjectCreate,
    db_session: DatabaseSessionDependency,
) -> ProjectRead:
    """Create one minimal Project scope behind both API-key boundaries."""

    return (await create_project(db_session, payload)).project


@router.patch(
    "/projects/{project_id}",
    response_model=ProjectRead,
    dependencies=internal_key,
)
async def patch_project(
    project_id: str,
    payload: ProjectUpdate,
    db_session: DatabaseSessionDependency,
) -> ProjectRead:
    """Update mutable Project display fields behind both API-key boundaries."""

    return await update_project(db_session, project_id, payload)


@router.patch(
    "/projects/{project_id}/memory-policy",
    response_model=ProjectRead,
    dependencies=internal_key,
)
async def patch_project_memory_policy(
    project_id: str,
    payload: ProjectMemoryPolicyUpdate,
    db_session: DatabaseSessionDependency,
) -> ProjectRead:
    """Explicitly enable, suspend, or disable Project Memory."""

    return await update_memory_policy(db_session, project_id, payload)


@router.post(
    "/projects/{project_id}/workspaces",
    response_model=ProjectWorkspaceRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=internal_key,
)
async def post_project_workspace(
    project_id: str,
    payload: ProjectWorkspaceCreate,
    db_session: DatabaseSessionDependency,
) -> ProjectWorkspaceRead:
    """Bind one credential-free workspace locator to a Project."""

    return await add_project_workspace(db_session, project_id, payload)


@router.post(
    "/projects/{project_id}/memory-candidates/{memory_id}/approve",
    response_model=MemoryRead,
    dependencies=internal_key,
)
async def post_approve_project_memory_candidate(
    project_id: str,
    memory_id: str,
    payload: CandidateDecisionCreate,
    db_session: DatabaseSessionDependency,
) -> MemoryRead:
    """Approve one Project candidate; Profile rebuilding remains an explicit operation."""

    return await approve_project_memory_candidate(
        db_session,
        project_id,
        memory_id,
        payload,
    )


@router.post(
    "/projects/{project_id}/memory/rebuild",
    response_model=ProjectMemoryProfileRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=internal_key,
)
async def post_project_memory_rebuild(
    project_id: str,
    payload: ProjectProfileRebuildCreate,
    response: Response,
    db_session: DatabaseSessionDependency,
    object_storage: ObjectStorageDependency,
) -> ProjectMemoryProfileRead:
    """Rebuild the Project core Profile from current formal facts."""

    result = await rebuild_project_profile(
        db_session,
        object_storage,
        project_id,
        expected_previous_version=payload.expected_previous_version,
        tokenizer_name=payload.tokenizer_name,
        tokenizer_version=payload.tokenizer_version,
        idempotency_key=payload.idempotency_key,
    )
    if result.was_replayed:
        response.status_code = status.HTTP_200_OK
    return result.record
