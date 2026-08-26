"""L2 Collaboration Memory internal controllers (dual-key)."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status

from nexuspilot_api.core.security import require_internal_api_key
from nexuspilot_api.features.memory.schemas.collaboration_memory import (
    AgentHandoffCreate,
    AgentHandoffRead,
    MemoryPacketRead,
    RunMemoryRebuildCreate,
    RunMemorySnapshotRead,
)
from nexuspilot_api.features.memory.services.collaboration_memory_service import (
    get_memory_packet,
    get_run_memory,
    list_handoffs,
    rebuild_run_memory,
    submit_handoff,
)
from nexuspilot_api.routers.common import (
    CursorCodecDependency,
    DatabaseSessionDependency,
    ObjectStorageDependency,
)
from nexuspilot_api.schemas.pagination import CursorPage

router = APIRouter(
    prefix="/internal",
    tags=["internal-collaboration-memory"],
    dependencies=[Depends(require_internal_api_key)],
)


@router.post(
    "/runs/{run_id}/agent-handoffs",
    response_model=AgentHandoffRead,
    status_code=status.HTTP_201_CREATED,
)
async def post_agent_handoff(
    run_id: str,
    payload: AgentHandoffCreate,
    response: Response,
    db_session: DatabaseSessionDependency,
    object_storage: ObjectStorageDependency,
) -> AgentHandoffRead:
    """Submit one immutable Handoff behind both API-key boundaries."""

    result = await submit_handoff(db_session, object_storage, run_id, payload)
    if result.was_replayed:
        response.status_code = status.HTTP_200_OK
    return result.record


@router.get(
    "/runs/{run_id}/agent-handoffs",
    response_model=CursorPage[AgentHandoffRead],
)
async def get_agent_handoffs(
    run_id: str,
    db_session: DatabaseSessionDependency,
    cursor_codec: CursorCodecDependency,
    cursor: Annotated[str | None, Query(max_length=2048)] = None,
    task_id: Annotated[str | None, Query(max_length=36)] = None,
    agent_run_id: Annotated[str | None, Query(max_length=36)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> CursorPage[AgentHandoffRead]:
    """Page immutable Handoffs for one Run with filter-bound cursors."""

    return await list_handoffs(
        db_session,
        run_id,
        codec=cursor_codec,
        cursor=cursor,
        task_id=task_id,
        agent_run_id=agent_run_id,
        limit=limit,
    )


@router.get(
    "/runs/{run_id}/memory",
    response_model=RunMemorySnapshotRead,
)
async def get_internal_run_memory(
    run_id: str,
    db_session: DatabaseSessionDependency,
) -> RunMemorySnapshotRead:
    """Return the current merged Run Memory Snapshot for one Run."""

    return await get_run_memory(db_session, run_id)


@router.post(
    "/runs/{run_id}/memory/rebuild",
    response_model=RunMemorySnapshotRead,
    status_code=status.HTTP_201_CREATED,
)
async def post_run_memory_rebuild(
    run_id: str,
    payload: RunMemoryRebuildCreate,
    response: Response,
    db_session: DatabaseSessionDependency,
    object_storage: ObjectStorageDependency,
) -> RunMemorySnapshotRead:
    """Merge a bounded Handoff set into a new immutable Run Memory Snapshot version."""

    result = await rebuild_run_memory(db_session, object_storage, run_id, payload)
    if result.was_replayed:
        response.status_code = status.HTTP_200_OK
    return result.record


@router.get(
    "/agent-runs/{agent_run_id}/memory-packet",
    response_model=MemoryPacketRead,
)
async def get_agent_memory_packet(
    agent_run_id: str,
    db_session: DatabaseSessionDependency,
    memory_packet_id: Annotated[str, Query(max_length=36)],
) -> MemoryPacketRead:
    """Return one fixed Memory Packet actually delivered to an Agent."""

    return await get_memory_packet(db_session, memory_packet_id)
