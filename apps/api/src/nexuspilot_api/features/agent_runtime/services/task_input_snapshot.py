"""Freeze candidate-owned request identities for independent, authorized review.

Use the existing snapshot registry and MinIO envelope. No model body is copied to
SQL and no arbitrary task ID supplied by a model grants cross-task access.
"""

import asyncio
import hashlib
import json

from sqlalchemy import select

from nexuspilot_api.core.errors import ResourceConflictError, ResourceNotFoundError
from nexuspilot_api.features.memory.services.snapshot_protocol import upload_and_register_snapshot
from nexuspilot_api.models import (
    LlmAgentWorkflowNodeExecution,
    LlmMemorySnapshotObject,
    LlmModelAttempt,
    LlmRun,
    LlmTask,
)

SCHEMA = "task_input_snapshot.v1"


async def load_task_input_snapshot_manifest(db, storage, *, run_id, snapshot_id):
    """Verify snapshot owner, task, schema and object hash before reading private references."""
    run = await db.get(LlmRun, run_id, populate_existing=True)
    row = await db.get(LlmMemorySnapshotObject, snapshot_id, populate_existing=True)
    if (run is None or row is None or row.run_id != run_id or row.user_id != run.user_id
            or row.schema_version != SCHEMA or row.mime_type != "application/json"):
        raise ResourceNotFoundError("Task input snapshot")
    task = await db.get(LlmTask, row.task_id, populate_existing=True)
    if task is None or task.run_id != run_id:
        raise ResourceNotFoundError("Task input snapshot")
    if not await storage.verify_object(row.storage_uri, row.content_hash):
        raise ResourceConflictError("Task input snapshot hash mismatch")
    obj = await storage.open_object(row.storage_uri)
    encoded = await asyncio.to_thread(b"".join, obj.chunks)
    if hashlib.sha256(encoded).hexdigest() != row.content_hash:
        raise ResourceConflictError("Task input snapshot changed while reading")
    try:
        envelope = json.loads(encoded)
        content = envelope["content"]
        if (envelope["schema_version"] != SCHEMA or content["run_id"] != run_id
                or content["task_id"] != row.task_id
                or envelope["object_id"] != content["candidate_node_id"]
                or not isinstance(content["attempts"], list)):
            raise ValueError("Invalid snapshot scope")
        identities = [item["attempt_id"] for item in content["attempts"]]
        if len(identities) != len(set(identities)):
            raise ValueError("Duplicate captured request")
    except (ValueError, KeyError, TypeError) as error:
        raise ResourceConflictError("Task input snapshot is invalid") from error
    node = await db.get(LlmAgentWorkflowNodeExecution, content["candidate_node_id"])
    if node is None or node.run_id != run_id or node.task_id != row.task_id:
        raise ResourceConflictError("Task input snapshot candidate is unavailable")
    return content


async def save_task_input_snapshot(db, storage, *, run_id, task_id, candidate_node_id):
    """Capture one fixed request list per candidate; replay returns the original observation."""
    task = await db.scalar(select(LlmTask).where(
        LlmTask.task_id == task_id,
    ).with_for_update())
    run = await db.get(LlmRun, run_id)
    node = await db.get(LlmAgentWorkflowNodeExecution, candidate_node_id)
    if (run is None or task is None or task.run_id != run_id or node is None
            or node.run_id != run_id or node.task_id != task_id):
        raise ResourceNotFoundError("Candidate task")
    object_type = f"task_inputs.{candidate_node_id}"
    existing = await db.scalar(select(LlmMemorySnapshotObject).where(
        LlmMemorySnapshotObject.run_id == run_id,
        LlmMemorySnapshotObject.task_id == task_id,
        LlmMemorySnapshotObject.schema_version == SCHEMA,
        LlmMemorySnapshotObject.object_type == object_type + ".json",
    ))
    if existing:
        content = await load_task_input_snapshot_manifest(
            db, storage, run_id=run_id, snapshot_id=existing.memory_snapshot_object_id,
        )
        snapshot_id = existing.memory_snapshot_object_id
    else:
        attempts = list(await db.scalars(select(LlmModelAttempt).where(
            LlmModelAttempt.run_id == run_id, LlmModelAttempt.task_id == task_id,
        ).order_by(LlmModelAttempt.started_at, LlmModelAttempt.attempt_id)))
        records = []
        for attempt in attempts:
            digest = None
            if attempt.raw_request_uri:
                obj = await storage.open_object(attempt.raw_request_uri)
                encoded = await asyncio.to_thread(b"".join, obj.chunks)
                digest = hashlib.sha256(encoded).hexdigest()
            records.append({
                "attempt_id": attempt.attempt_id, "status_at_capture": attempt.status.value,
                "request_uri": attempt.raw_request_uri, "request_sha256": digest,
            })
        content = {
            "run_id": run_id, "task_id": task_id, "candidate_node_id": candidate_node_id,
            "role": task.assigned_role, "attempts": records,
        }
        if len(json.dumps(content).encode()) > 1024 * 1024:
            raise ResourceConflictError("Task input snapshot exceeds 1 MiB")
        stored = await upload_and_register_snapshot(
            db_session=db, storage=storage, user_id=run.user_id,
            memory_layer="task_execution", scope_id=task_id, object_type=object_type,
            schema_version=SCHEMA, version=1, object_id=candidate_node_id,
            content=content, source_ids=[r["attempt_id"] for r in records],
            run_id=run_id, task_id=task_id, session_id=run.session_id,
        )
        snapshot_id = stored.json_object_id
    await db.commit()
    return {
        "snapshot_id": snapshot_id, "run_id": run_id, "task_id": task_id,
        "candidate_node_id": candidate_node_id, "role": content["role"],
        "attempt_count": len(content["attempts"]),
    }


async def read_task_input_snapshot(
    db, storage, *, run_id, requester_task_id, snapshot_id, allowed_snapshot_ids, offset, limit,
):
    """Read a bounded immutable request page granted explicitly by the candidate's coordinator."""
    from nexuspilot_api.features.agent_runtime.services.task_input_coverage import (
        summarize_attempt_input,
    )

    requester = await db.get(LlmTask, requester_task_id, populate_existing=True)
    if (requester is None or requester.run_id != run_id or snapshot_id not in allowed_snapshot_ids):
        raise ResourceNotFoundError("Authorized task input snapshot")
    content = await load_task_input_snapshot_manifest(
        db, storage, run_id=run_id, snapshot_id=snapshot_id,
    )
    records = []
    for saved in content["attempts"][offset:offset + limit]:
        attempt = await db.get(LlmModelAttempt, saved["attempt_id"], populate_existing=True)
        if (attempt is None or attempt.run_id != run_id or attempt.task_id != content["task_id"]
                or attempt.raw_request_uri != saved["request_uri"]):
            raise ResourceConflictError("Captured model request no longer matches its task")
        summary = await summarize_attempt_input(
            db, storage, run_id=run_id, attempt=attempt,
            expected_request_hash=saved["request_sha256"],
        )
        records.append({**summary, "status": saved["status_at_capture"]})
    total = len(content["attempts"])
    return {
        "schema_version": SCHEMA, "scope": "fixed_task_request_page", "snapshot_id": snapshot_id,
        "run_id": run_id, "task_id": content["task_id"],
        "candidate_node_id": content["candidate_node_id"], "attempt_count": total,
        "attempts": records, "next_offset": offset + limit if offset + limit < total else None,
        "provider_receipt_verified": False, "model_understanding_verified": False,
        "pagination_note": "Fixed request IDs and statuses at capture, ordered by started_at/ID. "
        "Later requests are excluded. This is the named task, not necessarily the calling task.",
    }
