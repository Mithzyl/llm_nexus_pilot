"""Page task-owned prepared request evidence from MinIO, without claiming model receipt."""

import asyncio
import hashlib
import json

from nexuspilot_models.contracts import Message
from sqlalchemy import select

from nexuspilot_api.core.errors import ResourceConflictError, ResourceNotFoundError
from nexuspilot_api.models import LlmModelAttempt, LlmRun, LlmTask, UserFile
from nexuspilot_api.services.conversation_file_service import conversation_file_ids
from nexuspilot_api.services.model_input_manifest import (
    RuntimeSourceInput,
    build_delegated_source_manifest,
    build_runtime_source_manifest,
    build_tool_input_manifest,
)


def summarize_input_request(raw):
    """Rebuild saved audits and union exact file/snapshot/block hashes, not model claims.

    Old absent audits are unknown even if messages can be reconstructed now.
    Hash mismatches or invalid runtime paths fail explicitly without source text in errors.
    """
    result = {"audit_complete": False, "missing_audits": [], "prepared_blocks": [],
              "block_occurrences": None, "provider_receipt_verified": False,
              "model_understanding_verified": False}
    if not isinstance(raw, dict):
        raise ResourceConflictError("Prepared model request is not an object")
    if "effective_messages" not in raw:
        result["missing_audits"] = ["effective_messages"]
        return result
    try:
        messages = [Message.model_validate(m) for m in raw["effective_messages"]]
        native = build_tool_input_manifest(messages)
        delegated = build_delegated_source_manifest(messages)
        audits = {"tool_input_manifest": native, "delegated_source_manifest": delegated}
        runtime = raw.get("runtime_source_manifest")
        if runtime is not None:
            runtime_input = RuntimeSourceInput(
                json.loads(messages[runtime["message_index"]].content),
                tuple(tuple(path) for path in runtime["source_paths"]),
            )
            runtime = build_runtime_source_manifest(messages, runtime_input)
        audits["runtime_source_manifest"] = runtime
        for key, value in audits.items():
            if key not in raw:
                result["missing_audits"].append(key)
            elif raw[key] != value:
                raise ValueError("Prepared audit mismatch")
        entries = [*native["file_inputs"], *(runtime or {}).get("sources", [])]
        entries.extend(source for item in delegated["delegations"] for source in item["sources"])
        unknown = any(e.get("body_status") == "unrecognized" for e in native["file_inputs"])
        unknown |= any(e["body_status"] == "unrecognized" for e in delegated["delegations"])
        blocks = {}
        occurrences = 0
        for entry in entries:
            for block in entry["blocks"]:
                item = {key: entry[key] for key in (
                    "file_id", "source_hash", "parsed_hash", "parser_version",
                )}
                item.update(block)
                blocks[json.dumps(item, sort_keys=True)] = item
                occurrences += 1
        result.update(audit_complete=not result["missing_audits"] and not unknown,
                      prepared_blocks=list(blocks.values()), block_occurrences=occurrences)
    except (ValueError, TypeError, KeyError, IndexError, AttributeError) as error:
        raise ResourceConflictError("Prepared model input evidence is invalid") from error
    return result


async def summarize_attempt_input(db, storage, *, run_id, attempt, expected_request_hash=None):
    """Verify one prepared request and reauthorize its files without returning body or URI."""
    run = await db.get(LlmRun, run_id, populate_existing=True)
    if run is None or attempt.run_id != run_id:
        raise ResourceNotFoundError("Model request")
    if attempt.raw_request_uri:
        content = await storage.open_object(attempt.raw_request_uri)
        encoded = await asyncio.to_thread(b"".join, content.chunks)
        if expected_request_hash and hashlib.sha256(encoded).hexdigest() != expected_request_hash:
            raise ResourceConflictError("Captured model request hash mismatch")
        try:
            raw = json.loads(encoded)
        except (ValueError, TypeError) as error:
            raise ResourceConflictError("Prepared model request is unreadable") from error
        summary = summarize_input_request(raw)
    else:
        summary = summarize_input_request({})
    allowed_files = await conversation_file_ids(db, run_id)
    for identity in {b["file_id"] for b in summary["prepared_blocks"]}:
        file = await db.get(UserFile, identity, populate_existing=True)
        if (identity not in allowed_files or file is None
                or file.user_id != run.user_id or file.status != "ready"):
            raise ResourceNotFoundError("File")
    snapshots = {}
    for block in summary.pop("prepared_blocks"):
        key = tuple(block[name] for name in (
            "file_id", "source_hash", "parsed_hash", "parser_version",
        ))
        snapshots.setdefault(key, []).append(block)
    summary["files"] = [{
        **dict(zip(
            ("file_id", "source_hash", "parsed_hash", "parser_version"), key, strict=True,
        )),
        "unique_prepared_blocks": len(blocks),
        "prepared_blocks_sha256": hashlib.sha256(json.dumps(
            sorted(blocks, key=lambda b: (b["block_id"], b["block_sha256"])),
            sort_keys=True, separators=(",", ":"),
        ).encode()).hexdigest(),
    } for key, blocks in snapshots.items()]
    return {"attempt_id": attempt.attempt_id, "status": attempt.status.value, **summary}


async def task_input_coverage(db, storage, *, run_id, task_id, arguments):
    """Read one bounded live page of the exact task's requests and reauthorize every file.

    Page totals are not entire-task coverage. Attempts added during pagination can
    change positions; consumers deduplicate by attempt_id. Never load other tasks'
    requests or expose source prose, private reasoning or storage URIs in the result.
    """
    run = await db.get(LlmRun, run_id, populate_existing=True)
    task = await db.get(LlmTask, task_id, populate_existing=True)
    if run is None or task is None or task.run_id != run_id:
        raise ResourceNotFoundError("Task")
    attempts = list(await db.scalars(select(LlmModelAttempt).where(
        LlmModelAttempt.run_id == run_id, LlmModelAttempt.task_id == task_id,
    ).order_by(LlmModelAttempt.started_at, LlmModelAttempt.attempt_id)
        .offset(arguments.offset).limit(arguments.limit + 1)))
    records = []
    for attempt in attempts[:arguments.limit]:
        records.append(await summarize_attempt_input(
            db, storage, run_id=run_id, attempt=attempt,
        ))
    return {"schema_version": "task_input_coverage.v1", "scope": "current_task_request_page",
            "run_id": run_id, "task_id": task_id,
            "attempts": records, "next_offset": arguments.offset + arguments.limit
            if len(attempts) > arguments.limit else None,
            "model_understanding_verified": False, "provider_receipt_verified": False,
            "pagination_note": "This is a live request page, not whole-task coverage. "
            "Deduplicate attempt IDs across pages; statuses and page positions can change."}


async def authorize_input_coverage_history(db, *, run_id, result):
    """Recheck file access before reusing stored input summaries in another model request."""
    run = await db.get(LlmRun, run_id, populate_existing=True)
    allowed = await conversation_file_ids(db, run_id)
    for identity in {file["file_id"] for attempt in result["attempts"]
                     for file in attempt["files"]}:
        file = await db.get(UserFile, identity, populate_existing=True)
        if (run is None or identity not in allowed or file is None
                or file.user_id != run.user_id or file.status != "ready"):
            raise ResourceConflictError("Prepared input file is no longer available")
