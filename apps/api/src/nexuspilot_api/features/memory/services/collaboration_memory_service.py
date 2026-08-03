"""L2 Collaboration Memory: Handoff, Run Snapshot, and Memory Packet use cases."""

import json
from dataclasses import dataclass

from sqlalchemy import and_, or_, select
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
    DatabaseQueryPaginationKey,
    database_query_fingerprint,
)
from nexuspilot_api.features.memory.schemas.collaboration_memory import (
    AgentHandoffCreate,
    AgentHandoffRead,
    MemoryPacketRead,
    RunMemoryRebuildCreate,
    RunMemorySnapshotRead,
)
from nexuspilot_api.features.memory.services.memory_policy import (
    estimate_memory_tokens,
    hash_memory_request,
)
from nexuspilot_api.features.memory.services.snapshot_protocol import (
    upload_and_register_snapshot,
)
from nexuspilot_api.infrastructure.object_storage import ObjectStorage
from nexuspilot_api.models import (
    HandoffStatus,
    LlmAgentHandoff,
    LlmAgentRun,
    LlmMemoryPacket,
    LlmMemoryPacketItem,
    LlmRun,
    LlmRunMemorySnapshot,
    LlmRunMemorySnapshotHandoff,
    LlmTask,
    SessionMemoryStatus,
    new_id,
)
from nexuspilot_api.models.base import utc_now
from nexuspilot_api.schemas.pagination import CursorPage

MAX_DIRECT_HANDOFFS = 4
RUN_MEMORY_STATE_HARD_CAP_TOKENS = 2_000
MAX_DIRECT_HANDOFF_TOKENS = 3_000


@dataclass(frozen=True)
class HandoffWriteResult:
    """Return one immutable Handoff and whether an idempotent request replayed."""

    record: AgentHandoffRead
    was_replayed: bool


@dataclass(frozen=True)
class SnapshotWriteResult:
    """Return one activated snapshot version and whether it replayed."""

    record: RunMemorySnapshotRead
    was_replayed: bool


async def submit_handoff(
    db_session: AsyncSession,
    storage: ObjectStorage,
    run_id: str,
    payload: AgentHandoffCreate,
) -> HandoffWriteResult:
    """Validate and persist one immutable Handoff with JSON/Markdown snapshot evidence."""

    run = await db_session.get(LlmRun, run_id)
    if run is None:
        raise ResourceNotFoundError("Run")
    agent_run = await db_session.get(LlmAgentRun, payload.agent_run_id)
    if agent_run is None:
        raise ResourceNotFoundError("Agent Run")
    if agent_run.run_id != run_id:
        raise ResourceConflictError("Agent Run does not belong to the Run")
    task_id = payload.task_id or agent_run.task_id
    if payload.task_id is not None:
        task = await db_session.get(LlmTask, payload.task_id)
        if task is None:
            raise ResourceNotFoundError("Task")
        if task.run_id != run_id:
            raise ResourceConflictError("Task does not belong to the Run")
    else:
        task_id = agent_run.task_id
    request_hash = hash_memory_request(payload.model_dump(mode="json"))
    replay = await db_session.scalar(
        select(LlmAgentHandoff).where(
            LlmAgentHandoff.agent_run_id == payload.agent_run_id,
            LlmAgentHandoff.idempotency_key == payload.idempotency_key,
        )
    )
    if replay is not None:
        if replay.request_hash != request_hash:
            raise ResourceConflictError(
                "Agent Handoff idempotency key was reused with another request"
            )
        return HandoffWriteResult(
            record=AgentHandoffRead.model_validate(replay),
            was_replayed=True,
        )
    handoff_id = new_id()
    superseded: LlmAgentHandoff | None = None
    if payload.supersedes_handoff_id is not None:
        superseded = await db_session.get(LlmAgentHandoff, payload.supersedes_handoff_id)
        if superseded is None:
            raise ResourceNotFoundError("Handoff")
        if superseded.run_id != run_id:
            raise ResourceConflictError("Superseded Handoff does not belong to the Run")
    try:
        registered = await upload_and_register_snapshot(
            db_session=db_session,
            storage=storage,
            user_id=run.user_id,
            memory_layer="l2",
            scope_id=run_id,
            object_type="agent_handoff",
            schema_version=payload.schema_version,
            version=1,
            object_id=handoff_id,
            content=payload.handoff_json,
            source_ids=[payload.agent_run_id],
            run_id=run_id,
            task_id=task_id,
            created_at=utc_now(),
        )
        if superseded is not None:
            superseded.status = HandoffStatus.SUPERSEDED
        handoff = LlmAgentHandoff(
            agent_handoff_id=handoff_id,
            agent_run_id=payload.agent_run_id,
            run_id=run_id,
            task_id=task_id,
            schema_version=payload.schema_version,
            version=1,
            status=payload.status,
            handoff_json=payload.handoff_json,
            supersedes_handoff_id=payload.supersedes_handoff_id,
            json_snapshot_object_id=registered.json_object_id,
            markdown_snapshot_object_id=registered.markdown_object_id,
            idempotency_key=payload.idempotency_key,
            request_hash=request_hash,
        )
        db_session.add(handoff)
        await db_session.commit()
    except (IntegrityError, OperationalError) as exc:
        await db_session.rollback()
        replay = await db_session.scalar(
            select(LlmAgentHandoff).where(
                LlmAgentHandoff.agent_run_id == payload.agent_run_id,
                LlmAgentHandoff.idempotency_key == payload.idempotency_key,
            )
        )
        if replay is not None:
            return HandoffWriteResult(
                record=AgentHandoffRead.model_validate(replay),
                was_replayed=True,
            )
        raise ResourceConflictError("Agent Handoff creation conflicted") from exc
    return HandoffWriteResult(
        record=AgentHandoffRead.model_validate(handoff),
        was_replayed=False,
    )


async def list_handoffs(
    db_session: AsyncSession,
    run_id: str,
    *,
    codec: CursorCodec,
    cursor: str | None,
    task_id: str | None,
    agent_run_id: str | None,
    limit: int,
) -> CursorPage[AgentHandoffRead]:
    """Return an owner-scoped, filter-bound page of immutable Handoffs."""

    run = await db_session.get(LlmRun, run_id)
    if run is None:
        raise ResourceNotFoundError("Run")
    query_fingerprint = database_query_fingerprint(
        "agent-handoffs",
        {
            "run_id": run_id,
            "task_id": task_id,
            "agent_run_id": agent_run_id,
        },
    )
    after_database_key = codec.decode_query(cursor) if cursor else None
    if after_database_key and after_database_key.query_fingerprint != query_fingerprint:
        raise InvalidCursorError
    statement = select(LlmAgentHandoff).where(LlmAgentHandoff.run_id == run_id)
    if task_id is not None:
        statement = statement.where(LlmAgentHandoff.task_id == task_id)
    if agent_run_id is not None:
        statement = statement.where(LlmAgentHandoff.agent_run_id == agent_run_id)
    if after_database_key is not None:
        cursor_time = after_database_key.created_at.astimezone().replace(tzinfo=None)
        statement = statement.where(
            or_(
                LlmAgentHandoff.created_at > cursor_time,
                and_(
                    LlmAgentHandoff.created_at == cursor_time,
                    LlmAgentHandoff.agent_handoff_id > after_database_key.identifier,
                ),
            )
        )
    handoffs = list(
        (
            await db_session.scalars(
                statement.order_by(
                    LlmAgentHandoff.created_at, LlmAgentHandoff.agent_handoff_id
                ).limit(limit + 1)
            )
        ).all()
    )
    has_more = len(handoffs) > limit
    items = handoffs[:limit]
    next_cursor = None
    if has_more and items:
        last_handoff = items[-1]
        next_cursor = codec.encode_query(
            DatabaseQueryPaginationKey(
                created_at=last_handoff.created_at,
                identifier=last_handoff.agent_handoff_id,
                query_fingerprint=query_fingerprint,
            )
        )
    return CursorPage[AgentHandoffRead](
        items=[AgentHandoffRead.model_validate(item) for item in items],
        next_cursor=next_cursor,
        has_more=next_cursor is not None,
        limit=limit,
    )


async def get_run_memory(
    db_session: AsyncSession,
    run_id: str,
) -> RunMemorySnapshotRead:
    """Return the current merged Run Memory Snapshot for one Run."""

    run = await db_session.get(LlmRun, run_id)
    if run is None:
        raise ResourceNotFoundError("Run")
    if run.current_memory_snapshot_id is None:
        raise ResourceNotFoundError("Run Memory Snapshot")
    snapshot = await db_session.get(LlmRunMemorySnapshot, run.current_memory_snapshot_id)
    if snapshot is None:
        raise ResourceConflictError("Run memory snapshot pointer is unavailable")
    return RunMemorySnapshotRead.model_validate(snapshot)


async def rebuild_run_memory(
    db_session: AsyncSession,
    storage: ObjectStorage,
    run_id: str,
    payload: RunMemoryRebuildCreate,
) -> SnapshotWriteResult:
    """Merge a bounded Handoff set into a new immutable Run Memory Snapshot version."""

    run = await db_session.scalar(select(LlmRun).where(LlmRun.run_id == run_id).with_for_update())
    if run is None:
        raise ResourceNotFoundError("Run")
    request_hash = hash_memory_request(payload.model_dump(mode="json"))
    replay = await db_session.scalar(
        select(LlmRunMemorySnapshot).where(
            LlmRunMemorySnapshot.run_id == run_id,
            LlmRunMemorySnapshot.idempotency_key == payload.idempotency_key,
        )
    )
    if replay is not None:
        if replay.request_hash != request_hash:
            raise ResourceConflictError(
                "Run memory idempotency key was reused with another request"
            )
        return SnapshotWriteResult(
            record=RunMemorySnapshotRead.model_validate(replay),
            was_replayed=True,
        )
    current_snapshot_id = run.current_memory_snapshot_id
    current_version_number = 0
    if current_snapshot_id is not None:
        current_snapshot = await db_session.get(LlmRunMemorySnapshot, current_snapshot_id)
        if current_snapshot is None:
            raise ResourceConflictError("Run memory snapshot pointer is unavailable")
        current_version_number = current_snapshot.version
    if payload.expected_previous_version != current_version_number:
        raise ResourceConflictError("Run memory version does not match expected_previous_version")
    handoff_ids = payload.handoff_ids
    if handoff_ids:
        handoffs = list(
            (
                await db_session.scalars(
                    select(LlmAgentHandoff)
                    .where(
                        LlmAgentHandoff.run_id == run_id,
                        LlmAgentHandoff.agent_handoff_id.in_(handoff_ids),
                    )
                    .order_by(LlmAgentHandoff.created_at, LlmAgentHandoff.agent_handoff_id)
                )
            ).all()
        )
        if len(handoffs) != len(set(handoff_ids)):
            raise InvalidRequestError("One or more Handoff ids do not belong to the Run")
    else:
        handoffs = list(
            (
                await db_session.scalars(
                    select(LlmAgentHandoff)
                    .where(
                        LlmAgentHandoff.run_id == run_id,
                        LlmAgentHandoff.status != HandoffStatus.SUPERSEDED,
                    )
                    .order_by(LlmAgentHandoff.created_at, LlmAgentHandoff.agent_handoff_id)
                )
            ).all()
        )
    state_json = _merge_handoff_state(handoffs)
    serialized_state = json.dumps(state_json, ensure_ascii=False, separators=(",", ":"))
    if estimate_memory_tokens(serialized_state) > RUN_MEMORY_STATE_HARD_CAP_TOKENS:
        raise InvalidRequestError(
            "Run Memory state exceeds the 2000-token hard cap and must not be truncated"
        )
    new_version_number = current_version_number + 1
    snapshot_id = new_id()
    try:
        registered = await upload_and_register_snapshot(
            db_session=db_session,
            storage=storage,
            user_id=run.user_id,
            memory_layer="l2",
            scope_id=run_id,
            object_type="run_memory_state",
            schema_version="run_memory_state.v1",
            version=new_version_number,
            object_id=snapshot_id,
            content=state_json,
            source_ids=[handoff.agent_handoff_id for handoff in handoffs],
            run_id=run_id,
            created_at=utc_now(),
        )
        if current_snapshot_id is not None:
            current_snapshot = await db_session.get(LlmRunMemorySnapshot, current_snapshot_id)
            if current_snapshot is not None:
                current_snapshot.status = SessionMemoryStatus.SUPERSEDED
        snapshot = LlmRunMemorySnapshot(
            run_memory_snapshot_id=snapshot_id,
            run_id=run_id,
            schema_version="run_memory_state.v1",
            version=new_version_number,
            status=SessionMemoryStatus.ACTIVE,
            state_json=state_json,
            previous_snapshot_id=current_snapshot_id,
            expected_previous_version=payload.expected_previous_version,
            json_snapshot_object_id=registered.json_object_id,
            markdown_snapshot_object_id=registered.markdown_object_id,
            idempotency_key=payload.idempotency_key,
            request_hash=request_hash,
            activated_at=utc_now(),
        )
        db_session.add(snapshot)
        run.current_memory_snapshot_id = snapshot_id
        db_session.add_all(
            [
                LlmRunMemorySnapshotHandoff(
                    run_memory_snapshot_id=snapshot_id,
                    agent_handoff_id=handoff.agent_handoff_id,
                    handoff_order=order,
                )
                for order, handoff in enumerate(handoffs, start=1)
            ]
        )
        await db_session.commit()
    except (IntegrityError, OperationalError) as exc:
        await db_session.rollback()
        replay = await db_session.scalar(
            select(LlmRunMemorySnapshot).where(
                LlmRunMemorySnapshot.run_id == run_id,
                LlmRunMemorySnapshot.idempotency_key == payload.idempotency_key,
            )
        )
        if replay is not None:
            return SnapshotWriteResult(
                record=RunMemorySnapshotRead.model_validate(replay),
                was_replayed=True,
            )
        raise ResourceConflictError("Run memory rebuild conflicted") from exc
    return SnapshotWriteResult(
        record=RunMemorySnapshotRead.model_validate(snapshot),
        was_replayed=False,
    )


async def get_memory_packet(
    db_session: AsyncSession,
    memory_packet_id: str,
) -> MemoryPacketRead:
    """Return one immutable Memory Packet with its ordered item evidence."""

    packet = await db_session.get(LlmMemoryPacket, memory_packet_id)
    if packet is None:
        raise ResourceNotFoundError("Memory Packet")
    items = list(
        (
            await db_session.scalars(
                select(LlmMemoryPacketItem)
                .where(LlmMemoryPacketItem.memory_packet_id == memory_packet_id)
                .order_by(LlmMemoryPacketItem.item_order)
            )
        ).all()
    )
    from nexuspilot_api.features.memory.schemas.collaboration_memory import (
        MemoryPacketItemRead,
    )

    return MemoryPacketRead(
        memory_packet_id=packet.memory_packet_id,
        agent_run_id=packet.agent_run_id,
        run_id=packet.run_id,
        task_id=packet.task_id,
        user_id=packet.user_id,
        schema_version=packet.schema_version,
        packet_json=packet.packet_json,
        estimated_token_count=packet.estimated_token_count,
        tokenizer_name=packet.tokenizer_name,
        tokenizer_version=packet.tokenizer_version,
        json_snapshot_object_id=packet.json_snapshot_object_id,
        created_at=packet.created_at,
        items=[MemoryPacketItemRead.model_validate(item) for item in items],
    )


def _merge_handoff_state(handoffs: list[LlmAgentHandoff]) -> dict:
    """Merge a bounded Handoff set into one deterministic run_memory_state.v1 document."""

    confirmed_facts: list[dict] = []
    decisions: list[dict] = []
    files_changed: list[dict] = []
    artifacts: list[dict] = []
    tests: list[dict] = []
    remaining_work: list[dict] = []
    risks: list[dict] = []
    conflicts: list[dict] = []
    invariants: list[dict] = []
    completed_work: list[dict] = []
    objective_parts: list[str] = []

    def _dedupe(target: list[dict], key: str, value: object, source: str) -> None:
        """Append one value unless an identical text already exists in the merge."""

        existing_texts = {item.get("text") for item in target}
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        if text not in existing_texts:
            target.append({"text": text, "source": source, key: text})

    for handoff in handoffs:
        handoff_data = handoff.handoff_json
        source = handoff.agent_handoff_id
        objective = str(handoff_data.get("objective") or "")
        if objective and objective not in objective_parts:
            objective_parts.append(objective)
        for fact in handoff_data.get("confirmed_facts", []):
            _dedupe(confirmed_facts, "fact", fact, source)
        for decision in handoff_data.get("decisions", []):
            _dedupe(decisions, "decision", decision, source)
        for changed_file in handoff_data.get("files_changed", []):
            _dedupe(files_changed, "file", changed_file, source)
        for artifact in handoff_data.get("artifacts", []):
            _dedupe(artifacts, "artifact", artifact, source)
        for test in handoff_data.get("tests", []):
            _dedupe(tests, "test", test, source)
        for work in handoff_data.get("remaining_work", []):
            _dedupe(remaining_work, "work", work, source)
        for risk in handoff_data.get("risks", []):
            _dedupe(risks, "risk", risk, source)
        for invariant in handoff_data.get("invariants_for_next_agent", []):
            _dedupe(invariants, "invariant", invariant, source)
        for declared_conflict in handoff_data.get("conflicts", []):
            conflicts.append({"text": declared_conflict, "source": source, "pending_review": True})
        completed_work.append({"summary": objective, "source": source})
    return {
        "objective": " | ".join(objective_parts),
        "confirmed_facts": confirmed_facts,
        "completed_work": completed_work,
        "key_decisions": decisions,
        "changed_files": files_changed,
        "artifacts": artifacts,
        "tests": tests,
        "remaining_work": remaining_work,
        "risks": risks,
        "conflicts": conflicts,
        "invariants": invariants,
    }
