"""Persist resumable tool dialogue in MinIO; SQL contains ownership and immutable references.

These snapshots are distinct from observation-only task_execution_checkpoint.v1.
They never grant permission to repeat an unknown provider or tool operation.
"""

import asyncio
import json
from time import monotonic, time

from nexuspilot_models.contracts import Message, ProviderName
from sqlalchemy import select

from nexuspilot_api.core.errors import ResourceConflictError
from nexuspilot_api.features.memory.services.snapshot_protocol import upload_and_register_snapshot
from nexuspilot_api.models import (
    LlmAgentWorkflowNodeExecution,
    LlmMemorySnapshotObject,
    LlmModelAttempt,
    LlmModelToolCall,
    LlmRun,
    new_id,
)
from nexuspilot_api.schemas.responses import ResponsesResult

SCHEMA = "agent_loop_resume.v1"


def encode_loop_state(state):
    """Save dialogue and progress, keeping private continuation and memory query text as refs."""
    from nexuspilot_api.features.memory.services.memory_tools import memory_checkpoint_message

    return {
        "history": [
            {
                "message": memory_checkpoint_message(message),
                "continuation_attempt_id": message._provider_continuation_parent_attempt_id,
            }
            for message in state.history
        ],
        "record_ids": [record.tool_call_id for record in state.records],
        "seen": state.seen,
        "progress": {
            "blocks": sorted(state.progress.blocks),
            "results": sorted(state.progress.results),
            "stalled": state.progress.stalled,
            "empty_searches": state.progress.empty_searches,
        },
        "loaded_versions": state.loaded_versions,
        "expires_at": time() + state.deadline - monotonic() if state.deadline else None,
        "model_round_count": state.model_round_count,
        "partial_continuation_count": state.partial_continuation_count,
        "model_pending": state.model_pending,
        "response": state.pending_response.model_dump(mode="json")
        if state.pending_response
        else None,
        "tool_index": state.tool_index,
        "assistant_added": state.assistant_added,
        "offered_versions": state.offered_versions,
    }


async def restore_loop_state(service, workflow, node, value):
    """Rebuild a task-owned loop and reload encrypted continuation under its original scope."""
    from nexuspilot_api.features.agent_runtime.services.agent_tool_loop import ToolLoopState

    state = ToolLoopState()
    state.seen = value["seen"]
    state.loaded_versions = value["loaded_versions"]
    state.progress.blocks = {tuple(block) for block in value["progress"]["blocks"]}
    state.progress.results = set(value["progress"]["results"])
    state.progress.stalled = value["progress"]["stalled"]
    state.progress.empty_searches = value["progress"]["empty_searches"]
    state.deadline = (
        monotonic() + value["expires_at"] - time() if value["expires_at"] is not None else None
    )
    state.model_round_count = value["model_round_count"]
    state.partial_continuation_count = value["partial_continuation_count"]
    state.model_pending = value["model_pending"]
    state.pending_response = (
        ResponsesResult.model_validate(value["response"]) if value["response"] else None
    )
    state.tool_index = value["tool_index"]
    state.assistant_added = value["assistant_added"]
    state.offered_versions = value["offered_versions"]
    db = service.model_invocation_service.db_session
    for identity in value["record_ids"]:
        record = await db.get(LlmModelToolCall, identity)
        attempt = await db.get(LlmModelAttempt, record.attempt_id) if record else None
        if attempt is None or attempt.run_id != workflow.run_id or attempt.task_id != node.task_id:
            raise ResourceConflictError("Checkpoint tool result scope mismatch")
        state.records.append(record)
    for saved in value["history"]:
        message = Message.model_validate(saved["message"])
        parent = saved["continuation_attempt_id"]
        if parent:
            attempt = await db.get(LlmModelAttempt, parent)
            if (
                attempt is None
                or attempt.run_id != workflow.run_id
                or attempt.task_id != node.task_id
            ):
                raise ResourceConflictError("Checkpoint continuation scope mismatch")
            message._provider_continuation_state = (
                await service.model_invocation_service.continuation_store.load_state(
                    parent_attempt_id=parent,
                    run_id=workflow.run_id,
                    task_id=node.task_id,
                    provider=ProviderName(attempt.provider),
                    model=attempt.model,
                )
            )
            message._provider_continuation_parent_attempt_id = parent
        state.history.append(message)
    return state


async def save_loop_checkpoint(db, storage, workflow, node, state, frame, *, phase):
    """Upload immutable execution state, then publish its SQL reference under Worker ownership."""
    run = await db.get(LlmRun, workflow.run_id)
    latest = await db.scalar(
        select(LlmMemorySnapshotObject)
        .where(
            LlmMemorySnapshotObject.run_id == workflow.run_id,
            LlmMemorySnapshotObject.schema_version == SCHEMA,
            LlmMemorySnapshotObject.mime_type == "application/json",
        )
        .order_by(LlmMemorySnapshotObject.object_version.desc())
        .limit(1)
    )
    content = {
        "workflow_id": workflow.workflow_execution_id,
        "node_id": node.node_execution_id,
        "phase": phase,
        "frame": frame,
        "loop": encode_loop_state(state),
    }
    if len(json.dumps(content, ensure_ascii=False).encode()) > 8 * 1024 * 1024:
        raise ResourceConflictError("Execution checkpoint exceeds 8 MiB")
    registered = await upload_and_register_snapshot(
        db_session=db,
        storage=storage,
        user_id=run.user_id,
        memory_layer="task_execution",
        scope_id=node.task_id,
        object_type="delegation_loop"
        if node.node_key.startswith("general_delegate.")
        else "loop_resume",
        schema_version=SCHEMA,
        version=latest.object_version + 1 if latest else 1,
        object_id=new_id(),
        content=content,
        source_ids=[node.node_execution_id],
        run_id=run.run_id,
        task_id=node.task_id,
        session_id=run.session_id,
    )
    await db.commit()
    return registered.json_object_id


async def load_loop_checkpoint(db, storage, workflow, *, node=None):
    """Load a parent or one explicitly scoped child checkpoint without replacing parent progress."""
    row = await db.scalar(
        select(LlmMemorySnapshotObject)
        .where(
            LlmMemorySnapshotObject.run_id == workflow.run_id,
            LlmMemorySnapshotObject.schema_version == SCHEMA,
            LlmMemorySnapshotObject.mime_type == "application/json",
            LlmMemorySnapshotObject.object_type
            == ("delegation_loop.json" if node else "loop_resume.json"),
            *([LlmMemorySnapshotObject.task_id == node.task_id] if node else []),
        )
        .order_by(LlmMemorySnapshotObject.object_version.desc())
        .limit(1)
    )
    if row is None:
        return None
    run = await db.get(LlmRun, workflow.run_id)
    if row.user_id != run.user_id or not await storage.verify_object(
        row.storage_uri, row.content_hash
    ):
        raise ResourceConflictError("Execution checkpoint owner or object mismatch")
    obj = await storage.open_object(row.storage_uri)
    envelope = json.loads(await asyncio.to_thread(b"".join, obj.chunks))
    value = envelope["content"]
    expected_node = node
    node = await db.get(LlmAgentWorkflowNodeExecution, value["node_id"])
    if (
        envelope["schema_version"] != SCHEMA
        or envelope["version"] != row.object_version
        or value["workflow_id"] != workflow.workflow_execution_id
        or node is None
        or node.workflow_execution_id != workflow.workflow_execution_id
        or node.task_id != row.task_id
        or (expected_node is not None and expected_node.node_execution_id != node.node_execution_id)
    ):
        raise ResourceConflictError("Execution checkpoint identity mismatch")
    if (
        node.node_key not in {"general_agent", "general_review"}
        and not node.node_key.startswith("general_delegate.")
    ) or node.status.value not in {"running", "completed"}:
        return None
    return value
