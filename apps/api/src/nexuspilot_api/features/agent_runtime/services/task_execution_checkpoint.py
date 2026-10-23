"""Immutable task execution observations, not permission to resume provider side effects."""

import asyncio
import hashlib
import json

from sqlalchemy import select

from nexuspilot_api.core.errors import ResourceConflictError, ResourceNotFoundError
from nexuspilot_api.features.memory.services.snapshot_protocol import upload_and_register_snapshot
from nexuspilot_api.models import LlmMemorySnapshotObject, LlmModelAttempt, LlmRun, LlmTask

SCHEMA = "task_execution_checkpoint.v1"


async def save_task_execution_checkpoint(db, storage, *, run_id, task_id, attempt_id,
                                         phase, tool_records, loaded_versions):
    """Lock the task, verify actual records, and register a bounded immutable observation.

    References identify saved originals; private model messages and reasoning are
    not copied. A repeated phase with the same references reuses its snapshot.
    Object upload precedes SQL commit, following the existing snapshot protocol.
    This does not dispatch tools, change Task state, or implement restart recovery.
    """
    task = await db.scalar(select(LlmTask).where(LlmTask.task_id == task_id).with_for_update())
    run = await db.get(LlmRun, run_id)
    attempt = await db.get(LlmModelAttempt, attempt_id)
    if run is None or task is None or task.run_id != run_id:
        raise ResourceNotFoundError("Task")
    if (attempt is None or attempt.run_id != run_id or attempt.task_id != task_id
            or not attempt.raw_request_uri):
        raise ResourceConflictError("Checkpoint model request does not belong to the task")
    if phase not in {"model_response", "tool_result"}:
        raise ResourceConflictError("Unknown task checkpoint phase")
    record_ids = [record.tool_call_id for record in tool_records]
    if len(record_ids) != len(set(record_ids)):
        raise ResourceConflictError("Duplicate checkpoint tool result")
    for record in tool_records:
        owner = await db.get(LlmModelAttempt, record.attempt_id)
        if owner is None or owner.run_id != run_id or owner.task_id != task_id:
            raise ResourceConflictError("Checkpoint tool result belongs to another task")
    key = hashlib.sha256(json.dumps(
        [attempt_id, phase, record_ids], sort_keys=True,
    ).encode()).hexdigest()
    object_type = "task_checkpoint." + key[:32]
    query = select(LlmMemorySnapshotObject).where(
        LlmMemorySnapshotObject.run_id == run_id,
        LlmMemorySnapshotObject.task_id == task_id,
        LlmMemorySnapshotObject.schema_version == SCHEMA,
        LlmMemorySnapshotObject.mime_type == "application/json",
    )
    state = {
        "run_id": run_id, "task_id": task_id, "attempt_id": attempt_id,
        "phase": phase, "objective": task.objective,
        "tool_result_ids": record_ids,
        "note_ids": [r.tool_call_id for r in tool_records
                     if r.tool_name == "save_task_note" and r.status == "completed"],
        "loaded_tool_versions": dict(sorted(loaded_versions.items())),
        "automatic_resume_supported": False,
        "provider_outcome": attempt.status.value,
    }
    replay = await db.scalar(query.where(
        LlmMemorySnapshotObject.object_type == object_type + ".json",
    ))
    if replay:
        previous = await read_task_execution_checkpoint(
            db, storage, run_id=run_id, task_id=task_id,
            snapshot_id=replay.memory_snapshot_object_id,
        )
        if previous["state"] != state:
            raise ResourceConflictError("Checkpoint identity was reused with different facts")
        await db.commit()
        return replay.memory_snapshot_object_id
    latest = await db.scalar(query.order_by(LlmMemorySnapshotObject.object_version.desc()).limit(1))
    content = {"state": state,
               "previous_snapshot_id": latest.memory_snapshot_object_id if latest else None}
    if len(json.dumps(content, ensure_ascii=False).encode()) > 1024 * 1024:
        raise ResourceConflictError("Task execution checkpoint exceeds 1 MiB")
    registered = await upload_and_register_snapshot(
        db_session=db, storage=storage, user_id=run.user_id,
        memory_layer="task_execution", scope_id=task_id, object_type=object_type,
        schema_version=SCHEMA, version=latest.object_version + 1 if latest else 1,
        object_id=key, content=content, source_ids=[attempt_id, *record_ids],
        run_id=run_id, task_id=task_id, session_id=run.session_id,
    )
    await db.commit()
    return registered.json_object_id


async def read_task_execution_checkpoint(db, storage, *, run_id, task_id, snapshot_id):
    """Read a run-scoped snapshot after checking its registered owner and task.

    Callers must authorize the Run before invoking this internal reader.
    """
    row = await db.get(LlmMemorySnapshotObject, snapshot_id, populate_existing=True)
    run = await db.get(LlmRun, run_id, populate_existing=True)
    task = await db.get(LlmTask, task_id, populate_existing=True)
    if (run is None or task is None or task.run_id != run_id
            or row is None or row.user_id != run.user_id
            or row.run_id != run_id or row.task_id != task_id
            or row.schema_version != SCHEMA or row.mime_type != "application/json"):
        raise ResourceNotFoundError("Task checkpoint")
    if not await storage.verify_object(row.storage_uri, row.content_hash):
        raise ResourceConflictError("Task checkpoint object hash mismatch")
    content = await storage.open_object(row.storage_uri)
    envelope = json.loads(await asyncio.to_thread(b"".join, content.chunks))
    if (envelope.get("schema_version") != SCHEMA
            or envelope.get("version") != row.object_version
            or envelope.get("content", {}).get("state", {}).get("run_id") != run_id
            or envelope.get("content", {}).get("state", {}).get("task_id") != task_id):
        raise ResourceConflictError("Task checkpoint envelope does not match its registration")
    return envelope["content"]
