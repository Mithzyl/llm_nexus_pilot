"""L4 User Memory Profile and candidate decision controllers."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status

from nexuspilot_api.core.security import require_internal_api_key
from nexuspilot_api.features.memory.schemas.memories import MemoryRead
from nexuspilot_api.features.memory.schemas.user_memory import (
    CandidateDecisionCreate,
    MemoryStatus,
    UserMemoryCandidateRead,
    UserMemoryProfileRead,
    UserMemoryProfileRebuildCreate,
)
from nexuspilot_api.features.memory.services.profile_snapshot_service import (
    get_user_profile,
    rebuild_user_profile,
)
from nexuspilot_api.features.memory.services.user_memory_service import (
    approve_user_memory_candidate,
    list_user_memory_candidates,
    reject_user_memory_candidate,
)
from nexuspilot_api.routers.common import (
    CursorCodecDependency,
    DatabaseSessionDependency,
    ObjectStorageDependency,
)
from nexuspilot_api.schemas.pagination import CursorPage

router = APIRouter(tags=["user-memory"])
internal_key = [Depends(require_internal_api_key)]


@router.get(
    "/users/{user_id}/memory-profile",
    response_model=UserMemoryProfileRead,
)
async def get_user_memory_profile(
    user_id: str,
    db_session: DatabaseSessionDependency,
) -> UserMemoryProfileRead:
    """Return the current User core Profile with token evidence."""

    return await get_user_profile(db_session, user_id)


@router.get(
    "/users/{user_id}/memory-candidates",
    response_model=CursorPage[UserMemoryCandidateRead],
)
async def get_user_memory_candidates(
    user_id: str,
    db_session: DatabaseSessionDependency,
    cursor_codec: CursorCodecDependency,
    cursor: Annotated[str | None, Query(max_length=2048)] = None,
    memory_status: Annotated[MemoryStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> CursorPage[UserMemoryCandidateRead]:
    """Page User Memory candidates and formal facts with filter-bound cursors."""

    return await list_user_memory_candidates(
        db_session,
        user_id,
        codec=cursor_codec,
        cursor=cursor,
        status=memory_status,
        limit=limit,
    )


@router.post(
    "/users/{user_id}/memory-candidates/{memory_id}/approve",
    response_model=MemoryRead,
    dependencies=internal_key,
)
async def post_approve_user_memory_candidate(
    user_id: str,
    memory_id: str,
    payload: CandidateDecisionCreate,
    db_session: DatabaseSessionDependency,
) -> MemoryRead:
    """Approve one User candidate; Profile rebuilding remains an explicit operation."""

    return await approve_user_memory_candidate(
        db_session,
        user_id,
        memory_id,
        payload,
    )


@router.post(
    "/users/{user_id}/memory-candidates/{memory_id}/reject",
    response_model=MemoryRead,
    dependencies=internal_key,
)
async def post_reject_user_memory_candidate(
    user_id: str,
    memory_id: str,
    payload: CandidateDecisionCreate,
    db_session: DatabaseSessionDependency,
) -> MemoryRead:
    """Reject one User candidate without touching the core Profile."""

    return await reject_user_memory_candidate(
        db_session,
        user_id,
        memory_id,
        payload,
    )


@router.post(
    "/users/{user_id}/memory-profile/rebuild",
    response_model=UserMemoryProfileRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=internal_key,
)
async def post_user_memory_profile_rebuild(
    user_id: str,
    payload: UserMemoryProfileRebuildCreate,
    response: Response,
    db_session: DatabaseSessionDependency,
    object_storage: ObjectStorageDependency,
) -> UserMemoryProfileRead:
    """Rebuild the User core Profile from current approved formal facts."""

    result = await rebuild_user_profile(
        db_session,
        object_storage,
        user_id,
        expected_previous_version=payload.expected_previous_version,
        tokenizer_name=payload.tokenizer_name,
        tokenizer_version=payload.tokenizer_version,
        idempotency_key=payload.idempotency_key,
    )
    if result.was_replayed:
        response.status_code = status.HTTP_200_OK
    return result.record
