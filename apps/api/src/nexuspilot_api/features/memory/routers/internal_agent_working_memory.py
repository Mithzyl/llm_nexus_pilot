"""L0 Agent Working Memory and Agent Run internal controllers (dual-key)."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status

from nexuspilot_api.core.security import require_internal_api_key
from nexuspilot_api.features.memory.schemas.agent_working_memory import (
    AgentRunCreate,
    AgentRunRead,
    AgentRunStatus,
    AgentWorkingStateCreate,
    AgentWorkingStateRead,
)
from nexuspilot_api.features.memory.services.agent_working_memory_service import (
    create_agent_run,
    create_working_state_checkpoint,
    finalize_agent_run,
    get_working_state,
    list_working_state_versions,
)
from nexuspilot_api.routers.common import (
    CursorCodecDependency,
    DatabaseSessionDependency,
)
from nexuspilot_api.schemas.pagination import CursorPage

router = APIRouter(
    prefix="/internal/agent-runs",
    tags=["internal-agent-working-memory"],
    dependencies=[Depends(require_internal_api_key)],
)


@router.post(
    "",
    response_model=AgentRunRead,
    status_code=status.HTTP_201_CREATED,
)
async def post_agent_run(
    payload: AgentRunCreate,
    db_session: DatabaseSessionDependency,
) -> AgentRunRead:
    """Create one role-owned Agent Run inside a validated Task scope."""

    return await create_agent_run(db_session, payload)


@router.post(
    "/{agent_run_id}/working-memory/checkpoints",
    response_model=AgentWorkingStateRead,
    status_code=status.HTTP_201_CREATED,
)
async def post_working_state_checkpoint(
    agent_run_id: str,
    payload: AgentWorkingStateCreate,
    response: Response,
    db_session: DatabaseSessionDependency,
) -> AgentWorkingStateRead:
    """Persist one versioned L0 check point and switch the recovery pointer."""

    result = await create_working_state_checkpoint(db_session, agent_run_id, payload)
    if result.was_replayed:
        response.status_code = status.HTTP_200_OK
    return result.record


@router.get(
    "/{agent_run_id}/working-memory",
    response_model=AgentWorkingStateRead,
)
async def get_agent_working_memory(
    agent_run_id: str,
    db_session: DatabaseSessionDependency,
) -> AgentWorkingStateRead:
    """Return the current recoverable state for one Agent Run."""

    return await get_working_state(db_session, agent_run_id)


@router.get(
    "/{agent_run_id}/working-memory/versions",
    response_model=CursorPage[AgentWorkingStateRead],
)
async def get_agent_working_memory_versions(
    agent_run_id: str,
    db_session: DatabaseSessionDependency,
    cursor_codec: CursorCodecDependency,
    cursor: Annotated[str | None, Query(max_length=2048)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> CursorPage[AgentWorkingStateRead]:
    """List immutable L0 check point versions in ascending version order."""

    return await list_working_state_versions(
        db_session,
        agent_run_id,
        codec=cursor_codec,
        cursor=cursor,
        limit=limit,
    )


@router.post(
    "/{agent_run_id}/status",
    response_model=AgentRunRead,
)
async def post_agent_run_status(
    agent_run_id: str,
    status_payload: Annotated[AgentRunStatus, Query(alias="status")],
    db_session: DatabaseSessionDependency,
) -> AgentRunRead:
    """Finalize one Agent Run and stop future working-state injection."""

    return await finalize_agent_run(
        db_session,
        agent_run_id,
        status=status_payload,
    )
