"""Memory Store, immutable version, and deterministic retrieval controllers."""

from typing import Annotated

from fastapi import APIRouter, Query, Response, status

from nexuspilot_api.features.memory.schemas.memories import (
    MemoryCreate,
    MemoryMutationRead,
    MemoryRead,
    MemoryRetrievalCreate,
    MemoryRetrievalRead,
    MemoryStatus,
    MemorySummary,
    MemoryType,
    MemoryUpdate,
    MemoryVersionRead,
)
from nexuspilot_api.features.memory.services.memory_retrieval_service import (
    get_memory_retrieval,
    retrieve_memories,
)
from nexuspilot_api.features.memory.services.memory_service import (
    create_memory,
    delete_memory,
    get_memory,
    list_memories,
    list_memory_mutations,
    list_memory_versions,
    update_memory,
)
from nexuspilot_api.routers.common import CursorCodecDependency, DatabaseSessionDependency
from nexuspilot_api.schemas.pagination import CursorPage

router = APIRouter(tags=["memories"])


@router.post("/memories", response_model=MemoryRead, status_code=status.HTTP_201_CREATED)
async def post_memory(
    payload: MemoryCreate,
    response: Response,
    db_session: DatabaseSessionDependency,
) -> MemoryRead:
    """Create one sourced Memory or replay an identical idempotent creation."""

    result = await create_memory(db_session, payload)
    if result.was_replayed:
        response.status_code = status.HTTP_200_OK
    return result.memory


@router.get("/memories", response_model=CursorPage[MemorySummary])
async def get_memories(
    user_id: Annotated[str, Query(min_length=1, max_length=128)],
    db_session: DatabaseSessionDependency,
    cursor_codec: CursorCodecDependency,
    cursor: str | None = None,
    session_id: str | None = None,
    run_id: str | None = None,
    task_id: str | None = None,
    memory_type: MemoryType | None = None,
    memory_status: Annotated[MemoryStatus | None, Query(alias="status")] = None,
    limit: int = Query(default=20, ge=1, le=100),
) -> CursorPage[MemorySummary]:
    """List current Memory versions inside a validated explicit user scope."""

    return await list_memories(
        db_session,
        codec=cursor_codec,
        cursor=cursor,
        user_id=user_id,
        session_id=session_id,
        run_id=run_id,
        task_id=task_id,
        memory_type=memory_type,
        memory_status=memory_status,
        limit=limit,
    )


@router.get("/memories/{memory_id}", response_model=MemoryRead)
async def get_memory_by_id(
    memory_id: str,
    db_session: DatabaseSessionDependency,
) -> MemoryRead:
    """Return one stable Memory and its current immutable version."""

    return await get_memory(db_session, memory_id)


@router.get(
    "/memories/{memory_id}/versions",
    response_model=CursorPage[MemoryVersionRead],
)
async def get_memory_versions(
    memory_id: str,
    db_session: DatabaseSessionDependency,
    cursor_codec: CursorCodecDependency,
    cursor: str | None = None,
    limit: int = Query(default=20, ge=1, le=100),
) -> CursorPage[MemoryVersionRead]:
    """List immutable content versions and their ordered source evidence."""

    return await list_memory_versions(
        db_session,
        memory_id,
        codec=cursor_codec,
        cursor=cursor,
        limit=limit,
    )


@router.get(
    "/memories/{memory_id}/mutations",
    response_model=CursorPage[MemoryMutationRead],
)
async def get_memory_mutations(
    memory_id: str,
    db_session: DatabaseSessionDependency,
    cursor_codec: CursorCodecDependency,
    cursor: str | None = None,
    limit: int = Query(default=20, ge=1, le=100),
) -> CursorPage[MemoryMutationRead]:
    """List the immutable mutation audit ledger for one stable Memory."""

    return await list_memory_mutations(
        db_session,
        memory_id,
        codec=cursor_codec,
        cursor=cursor,
        limit=limit,
    )


@router.patch("/memories/{memory_id}", response_model=MemoryRead)
async def patch_memory(
    memory_id: str,
    payload: MemoryUpdate,
    db_session: DatabaseSessionDependency,
) -> MemoryRead:
    """Apply one optimistic, idempotent Memory correction or lifecycle transition."""

    return (await update_memory(db_session, memory_id, payload)).memory


@router.delete("/memories/{memory_id}", response_model=MemoryRead)
async def remove_memory(
    memory_id: str,
    db_session: DatabaseSessionDependency,
) -> MemoryRead:
    """Idempotently delete one Memory and erase inline text and search terms."""

    return await delete_memory(db_session, memory_id)


@router.post(
    "/memory-retrievals",
    response_model=MemoryRetrievalRead,
    status_code=status.HTTP_201_CREATED,
)
async def post_memory_retrieval(
    payload: MemoryRetrievalCreate,
    response: Response,
    db_session: DatabaseSessionDependency,
) -> MemoryRetrievalRead:
    """Run and persist one deterministic owner-scoped Memory retrieval."""

    result = await retrieve_memories(db_session, payload)
    if result.was_replayed:
        response.status_code = status.HTTP_200_OK
    return result.retrieval


@router.get(
    "/memory-retrievals/{memory_retrieval_id}",
    response_model=MemoryRetrievalRead,
)
async def get_memory_retrieval_by_id(
    memory_retrieval_id: str,
    db_session: DatabaseSessionDependency,
) -> MemoryRetrievalRead:
    """Return one persisted retrieval with ranking and budget evidence."""

    return await get_memory_retrieval(db_session, memory_retrieval_id)
