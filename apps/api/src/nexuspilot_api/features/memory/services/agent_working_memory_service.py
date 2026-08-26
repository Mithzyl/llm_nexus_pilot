"""L0 Agent Working Memory transactional use cases."""

import json
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from nexuspilot_api.core.errors import (
    InvalidCursorError,
    InvalidRequestError,
    ResourceConflictError,
    ResourceNotFoundError,
)
from nexuspilot_api.core.pagination import (
    CursorCodec,
    DatabaseSequencePaginationKey,
)
from nexuspilot_api.features.memory.schemas.agent_working_memory import (
    AgentRunCreate,
    AgentRunRead,
    AgentWorkingStateCreate,
    AgentWorkingStateRead,
)
from nexuspilot_api.features.memory.services.memory_policy import hash_memory_request
from nexuspilot_api.models import (
    AgentRunStatus,
    AgentWorkingStateStatus,
    LlmAgentRun,
    LlmAgentTurn,
    LlmAgentWorkingStateVersion,
    LlmRun,
    LlmTask,
    new_id,
)
from nexuspilot_api.models.base import utc_now
from nexuspilot_api.schemas.pagination import CursorPage

L0_WORKING_STATE_HARD_CAP_TOKENS = 800


@dataclass(frozen=True)
class AgentWorkingStateWriteResult:
    """Return one check point version and whether an idempotent request replayed."""

    record: AgentWorkingStateRead
    was_replayed: bool


async def create_agent_run(
    db_session: AsyncSession,
    payload: AgentRunCreate,
) -> AgentRunRead:
    """Create one role-owned Agent Run after validating the Run/Task ownership chain."""

    run = await db_session.get(LlmRun, payload.run_id)
    if run is None:
        raise ResourceNotFoundError("Run")
    task = await db_session.get(LlmTask, payload.task_id)
    if task is None:
        raise ResourceNotFoundError("Task")
    if task.run_id != run.run_id:
        raise ResourceConflictError("Task does not belong to the Run")
    agent_run = LlmAgentRun(
        agent_run_id=new_id(),
        run_id=run.run_id,
        task_id=task.task_id,
        agent_role=payload.agent_role,
        status=AgentRunStatus.PENDING,
        provider=payload.provider,
        model=payload.model,
        current_turn_sequence=0,
    )
    db_session.add(agent_run)
    try:
        await db_session.commit()
    except IntegrityError as exc:
        await db_session.rollback()
        raise ResourceConflictError("Agent Run already exists for this Task and role") from exc
    await db_session.refresh(agent_run)
    return AgentRunRead.model_validate(agent_run)


async def create_working_state_checkpoint(
    db_session: AsyncSession,
    agent_run_id: str,
    payload: AgentWorkingStateCreate,
) -> AgentWorkingStateWriteResult:
    """Persist one L0 check point and switch the Agent Run recovery pointer."""

    try:
        return await _checkpoint_transaction(db_session, agent_run_id, payload)
    except OperationalError as exc:
        await db_session.rollback()
        if not _is_database_concurrency_conflict(exc):
            raise
        raise ResourceConflictError(
            "Agent working state checkpoint conflicted with a concurrent request"
        ) from exc


async def _checkpoint_transaction(
    db_session: AsyncSession,
    agent_run_id: str,
    payload: AgentWorkingStateCreate,
) -> AgentWorkingStateWriteResult:
    """Execute one optimistic check point under the Agent Run row lock."""

    agent_run = await db_session.scalar(
        select(LlmAgentRun).where(LlmAgentRun.agent_run_id == agent_run_id).with_for_update()
    )
    if agent_run is None:
        raise ResourceNotFoundError("Agent Run")
    if agent_run.status in {
        AgentRunStatus.COMPLETED,
        AgentRunStatus.CANCELLED,
        AgentRunStatus.FAILED,
    }:
        raise ResourceConflictError(f"Cannot checkpoint a {agent_run.status.value} Agent Run")
    request_hash = hash_memory_request(payload.model_dump(mode="json"))
    replay = await db_session.scalar(
        select(LlmAgentWorkingStateVersion).where(
            LlmAgentWorkingStateVersion.agent_run_id == agent_run_id,
            LlmAgentWorkingStateVersion.idempotency_key == payload.idempotency_key,
        )
    )
    if replay is not None:
        if replay.request_hash != request_hash:
            raise ResourceConflictError(
                "Agent working state idempotency key was reused with another request"
            )
        return AgentWorkingStateWriteResult(
            record=AgentWorkingStateRead.model_validate(replay),
            was_replayed=True,
        )
    previous_version_id = agent_run.current_working_state_version_id
    previous_version_number = 0
    if previous_version_id is not None:
        previous_version = await db_session.get(LlmAgentWorkingStateVersion, previous_version_id)
        if previous_version is None:
            raise ResourceConflictError("Agent working state pointer is unavailable")
        previous_version_number = previous_version.version
        if payload.expected_previous_version != previous_version_number:
            raise ResourceConflictError(
                "Agent working state version does not match expected_previous_version"
            )
        previous_version.status = AgentWorkingStateStatus.SUPERSEDED
    elif payload.expected_previous_version != 0:
        raise ResourceConflictError(
            "Agent working state version does not match expected_previous_version"
        )
    if payload.agent_turn_id is not None:
        turn = await db_session.get(LlmAgentTurn, payload.agent_turn_id)
        if turn is None:
            raise ResourceNotFoundError("Agent Turn")
        if turn.agent_run_id != agent_run_id:
            raise ResourceConflictError("Agent Turn does not belong to the Agent Run")
    state_text = json.dumps(payload.state_json, ensure_ascii=False, separators=(",", ":"))
    estimated_tokens = max(1, len(state_text.encode()))
    if estimated_tokens > L0_WORKING_STATE_HARD_CAP_TOKENS:
        raise InvalidRequestError(
            "Agent working state exceeds the 800-token hard cap and must not be truncated"
        )
    if payload.expires_at is not None and payload.expires_at.tzinfo is None:
        raise InvalidRequestError("expires_at must include a timezone")
    new_version_number = await _next_working_state_version(db_session, agent_run_id)
    working_state_version = LlmAgentWorkingStateVersion(
        agent_working_state_version_id=new_id(),
        agent_run_id=agent_run_id,
        agent_turn_id=payload.agent_turn_id,
        version=new_version_number,
        status=AgentWorkingStateStatus.ACTIVE,
        state_json=payload.state_json,
        previous_version_id=previous_version_id,
        estimated_token_count=estimated_tokens,
        tokenizer_name=payload.tokenizer_name,
        tokenizer_version=payload.tokenizer_version,
        idempotency_key=payload.idempotency_key,
        request_hash=request_hash,
        expires_at=payload.expires_at,
    )
    db_session.add(working_state_version)
    agent_run.current_working_state_version_id = (
        working_state_version.agent_working_state_version_id
    )
    try:
        await db_session.commit()
    except IntegrityError as exc:
        await db_session.rollback()
        raise ResourceConflictError("Agent working state version conflict") from exc
    return AgentWorkingStateWriteResult(
        record=AgentWorkingStateRead.model_validate(working_state_version),
        was_replayed=False,
    )


async def get_working_state(
    db_session: AsyncSession,
    agent_run_id: str,
) -> AgentWorkingStateRead:
    """Return the current recoverable check point for one Agent Run."""

    agent_run = await db_session.get(LlmAgentRun, agent_run_id)
    if agent_run is None:
        raise ResourceNotFoundError("Agent Run")
    if agent_run.current_working_state_version_id is None:
        raise ResourceNotFoundError("Agent Working State")
    version = await db_session.get(
        LlmAgentWorkingStateVersion, agent_run.current_working_state_version_id
    )
    if version is None:
        raise ResourceConflictError("Agent working state pointer is unavailable")
    return AgentWorkingStateRead.model_validate(version)


async def list_working_state_versions(
    db_session: AsyncSession,
    agent_run_id: str,
    *,
    codec: CursorCodec,
    cursor: str | None,
    limit: int,
) -> CursorPage[AgentWorkingStateRead]:
    """Return immutable L0 check point versions in ascending version order."""

    agent_run = await db_session.get(LlmAgentRun, agent_run_id)
    if agent_run is None:
        raise ResourceNotFoundError("Agent Run")
    after_database_key = codec.decode_sequence(cursor) if cursor else None
    if after_database_key and after_database_key.scope_id != agent_run_id:
        raise InvalidCursorError
    statement = select(LlmAgentWorkingStateVersion).where(
        LlmAgentWorkingStateVersion.agent_run_id == agent_run_id
    )
    if after_database_key is not None:
        statement = statement.where(
            LlmAgentWorkingStateVersion.version > after_database_key.sequence
        )
    versions = list(
        (
            await db_session.scalars(
                statement.order_by(LlmAgentWorkingStateVersion.version).limit(limit + 1)
            )
        ).all()
    )
    has_more = len(versions) > limit
    items = versions[:limit]
    next_cursor = None
    if has_more and items:
        next_cursor = codec.encode_sequence(
            DatabaseSequencePaginationKey(
                scope_id=agent_run_id,
                sequence=items[-1].version,
            )
        )
    return CursorPage[AgentWorkingStateRead](
        items=[AgentWorkingStateRead.model_validate(item) for item in items],
        next_cursor=next_cursor,
        has_more=next_cursor is not None,
        limit=limit,
    )


async def finalize_agent_run(
    db_session: AsyncSession,
    agent_run_id: str,
    *,
    status: AgentRunStatus,
) -> AgentRunRead:
    """Set one active Agent Run to a terminal status and finalize its checkpoint."""

    if status not in {
        AgentRunStatus.COMPLETED,
        AgentRunStatus.FAILED,
        AgentRunStatus.CANCELLED,
    }:
        raise InvalidRequestError("Agent Run final status must be terminal")

    agent_run = await db_session.scalar(
        select(LlmAgentRun).where(LlmAgentRun.agent_run_id == agent_run_id).with_for_update()
    )
    if agent_run is None:
        raise ResourceNotFoundError("Agent Run")
    if agent_run.status not in {
        AgentRunStatus.PENDING,
        AgentRunStatus.RUNNING,
        AgentRunStatus.WAITING,
    }:
        raise ResourceConflictError(f"Agent Run is already {agent_run.status.value}")
    agent_run.status = status
    agent_run.completed_at = utc_now()
    current_version_id = agent_run.current_working_state_version_id
    if current_version_id is not None:
        current_version = await db_session.get(LlmAgentWorkingStateVersion, current_version_id)
        if current_version is not None:
            current_version.status = AgentWorkingStateStatus.FINALIZED
    await db_session.commit()
    await db_session.refresh(agent_run)
    return AgentRunRead.model_validate(agent_run)


async def _next_working_state_version(
    db_session: AsyncSession,
    agent_run_id: str,
) -> int:
    """Return the next monotonic check point version for one Agent Run."""

    current_version = await db_session.scalar(
        select(LlmAgentWorkingStateVersion.version)
        .where(LlmAgentWorkingStateVersion.agent_run_id == agent_run_id)
        .order_by(LlmAgentWorkingStateVersion.version.desc())
        .limit(1)
    )
    return (current_version or 0) + 1


def _is_database_concurrency_conflict(error: OperationalError) -> bool:
    """Identify MySQL deadlock and lock-timeout errors that are safe to convert."""

    original_error = error.orig
    error_arguments = getattr(original_error, "args", ())
    return bool(error_arguments and error_arguments[0] in {1205, 1213})
