"""Authorize task-local Python execution and publish selected outputs as private Run Artifacts.

Docker access belongs to the separate Unix-socket executor. No shell command,
host path, identity, or credential is accepted from the model. Each invocation
uses a new process with the task's last saved file snapshot and freshly authorized inputs.
"""

import asyncio
import base64
import json
from contextlib import nullcontext
from pathlib import PurePosixPath
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.ext.asyncio import async_sessionmaker

from nexuspilot_api.core.config import get_settings
from nexuspilot_api.core.errors import ResourceConflictError
from nexuspilot_api.features.identity.models.applications import RunApplicationContext
from nexuspilot_api.models import LlmRun, LlmRunArtifact, LlmTask, TaskStatus, User, UserFile
from nexuspilot_api.services.agent_file_tool_service import FileToolResult, ToolOperationError
from nexuspilot_api.services.conversation_file_service import conversation_file_ids
from nexuspilot_api.services.run_artifact_service import create_run_artifact
from nexuspilot_api.services.tool_catalog import RegisteredTool
from nexuspilot_api.services.user_file_service import read_verified_bytes

MAX_WIRE_BYTES = 24 * 1024 * 1024


class PythonArguments(BaseModel):
    """Accept Python source and authorized object IDs, never an interpreter or owner override."""

    model_config = ConfigDict(extra="forbid")
    executable: Literal["python"] = "python"
    stdin: str = Field(min_length=1, max_length=65536)
    file_ids: list[str] = Field(default_factory=list, max_length=8)
    artifact_ids: list[str] = Field(default_factory=list, max_length=8)
    output_files: list[str] = Field(default_factory=list, max_length=8)

    @field_validator("stdin")
    @classmethod
    def bound_source(cls, value):
        """Bound UTF-8 source bytes to the executor stdin capacity."""
        if len(value.encode()) > 65536:
            raise ValueError("Source exceeds byte limit")
        return value

    @field_validator("output_files")
    @classmethod
    def relative_outputs(cls, values):
        """Allow only distinct ordinary relative paths within the writable task directory."""
        for value in values:
            if not value or len(value.encode()) > 240 or value.startswith("/") or (
                "\\" in value or any(ord(c) < 32 or ord(c) == 127 for c in value)
                or any(p in {"", ".", ".."} for p in value.split("/"))
            ):
                raise ValueError("Invalid output path")
        if len(set(values)) != len(values):
            raise ValueError("Duplicate output path")
        return values


PYTHON_TOOL = RegisteredTool(
    name="run_command", version="1.0.0", arguments=PythonArguments, output=FileToolResult,
    description="Run Python for code verification or deterministic data calculation, offline, "
    "30 seconds maximum. Files in /workspace continue within the same task from its last saved "
    "snapshot; Python variables do not persist. Cancelled calls do not replace that snapshot. "
    "Authorized file_ids and same-task artifact_ids are freshly mounted read-only "
    "at /input/<ID>. Select output_files relative to /workspace to save as private artifacts. "
    "Use existing file search/read tools for document reading. No Shell, network or pip. "
    "Results prove only the executed sample, not general code correctness.",
    required_capabilities=frozenset({"python_execute"}), risk_level="code_execution",
    execution_type="task_container",
    timeout_seconds=None, max_result_bytes=512 * 1024,
    keywords=("python", "代码", "计算", "验证", "csv", "calculate", "test"),
)


async def python_authorized(db, run_id, task_id):
    """Require a configured explicit owner grant and a currently running native task."""
    settings = get_settings()
    run = await db.get(LlmRun, run_id, populate_existing=True)
    task = await db.get(LlmTask, task_id, populate_existing=True)
    if not run or not task or task.run_id != run_id or not settings.python_runtime_socket:
        return False
    owner = await db.get(User, run.user_id, populate_existing=True)
    return bool(
        owner and owner.is_active and run.user_id in settings.python_execution_user_ids
        and run.status.value in {"pending", "running"} and task.status == TaskStatus.RUNNING
        and await db.get(RunApplicationContext, run_id) is None
    )


async def execute_python_request(socket_path, request):
    """Send once; cancellation waits for confirmed container cleanup without retrying code."""
    writer = None
    pending = None
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_unix_connection(socket_path, limit=MAX_WIRE_BYTES + 1), 5,
        )
        encoded = json.dumps(request).encode() + b"\n"
        if len(encoded) > MAX_WIRE_BYTES:
            raise ToolOperationError("python_input_too_large")
        async def receive():
            """Read one complete bounded report; EOF or a protocol error is never success."""
            line = await reader.readline()
            if len(line) > MAX_WIRE_BYTES or not line.endswith(b"\n"):
                raise ValueError("Invalid executor response")
            result = json.loads(line)
            if not isinstance(result, dict):
                raise ValueError("Invalid executor result")
            return result

        pending = asyncio.create_task(receive())
        try:
            async with asyncio.timeout(180):
                writer.write(encoded)
                await writer.drain()
                return await asyncio.shield(pending)
        except (asyncio.CancelledError, TimeoutError) as interrupted:
            try:
                async with asyncio.timeout(120):
                    writer.write(b'{"cancel":true}\n')
                    await writer.drain()
                    result = await asyncio.shield(pending)
                if result.get("container_removed") is not True:
                    raise ValueError("Stop unconfirmed")
            except (Exception, asyncio.CancelledError) as exc:
                raise ToolOperationError("python_stop_unconfirmed") from exc
            if isinstance(interrupted, TimeoutError):
                raise ToolOperationError(
                    "python_runtime_timeout", status="timed_out",
                ) from interrupted
            raise
    except (OSError, ValueError, asyncio.IncompleteReadError) as exc:
        raise ToolOperationError("python_runtime_unavailable") from exc
    finally:
        if pending and not pending.done():
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
        if writer:
            writer.close()
            await writer.wait_closed()


async def run_python(db, storage, task_id, attempt, record, arguments):
    """Persist authorized outputs, then acknowledge their committed identities to the executor."""
    if attempt.task_id != task_id or not await python_authorized(db, attempt.run_id, task_id):
        raise ToolOperationError("python_not_authorized", status="permission_denied")
    run = await db.get(LlmRun, attempt.run_id)
    allowed = await conversation_file_ids(db, run.run_id)
    inputs = {}
    total = 0
    for kind, ids in (("file", arguments.file_ids), ("artifact", arguments.artifact_ids)):
        for identity in ids:
            if kind == "file":
                item = await db.get(UserFile, identity, populate_existing=True)
                valid = item and identity in allowed and item.user_id == run.user_id and (
                    item.status == "ready"
                )
            else:
                item = await db.get(LlmRunArtifact, identity)
                valid = item and item.run_id == run.run_id and item.task_id == task_id
            if not valid:
                raise ToolOperationError("python_input_not_authorized", status="permission_denied")
            total += item.size_bytes
            if item.size_bytes > 8 * 1024 * 1024 or total > 16 * 1024 * 1024:
                raise ToolOperationError("python_input_too_large")
            raw = await read_verified_bytes(
                storage, item.storage_uri, item.size_bytes, item.content_hash,
            )
            inputs[identity] = base64.b64encode(raw).decode()
    if len(inputs) > 8:
        raise ToolOperationError("python_input_too_large")
    result = await execute_authorized_python(db, run.run_id, task_id, {
        "scope": {"user_id": run.user_id, "run_id": run.run_id, "task_id": task_id},
        "call_id": record.tool_call_id,
        "command": {"executable": "python", "stdin": arguments.stdin},
        "inputs": inputs, "output_files": arguments.output_files,
    })
    if result.get("error") == "python_result_unavailable":
        raise ToolOperationError("python_result_unavailable")
    if not await python_authorized(db, run.run_id, task_id):
        raise ToolOperationError("python_not_authorized", status="permission_denied")
    if result.get("container_removed") is not True or result.get("status") not in {
        "completed", "failed", "timed_out", "cancelled",
    }:
        raise ToolOperationError("python_execution_unavailable")
    outputs = result.get("files", {})
    if not isinstance(outputs, dict) or set(outputs) - set(arguments.output_files):
        raise ToolOperationError("python_invalid_result")
    # Decode and validate everything before publishing the first artifact.
    decoded = {}
    for path, content in outputs.items():
        try:
            decoded[path] = base64.b64decode(content, validate=True)
        except (ValueError, TypeError) as exc:
            raise ToolOperationError("python_invalid_result") from exc
    if any(len(b) > 8 * 1024 * 1024 for b in decoded.values()) or sum(
        map(len, decoded.values())
    ) > 16 * 1024 * 1024:
        raise ToolOperationError("python_invalid_result")
    report = {key: result.get(key) for key in (
        "status", "exit_code", "stdout", "stderr", "stdout_truncated", "stderr_truncated",
    )}
    if any(type(report[k]) is not bool for k in ("stdout_truncated", "stderr_truncated")):
        raise ToolOperationError("python_invalid_result")
    if result["status"] == "completed" and (
        type(report["exit_code"]) is not int or report["exit_code"] != 0
    ):
        raise ToolOperationError("python_invalid_result")
    for field in ("stdout", "stderr"):
        if not isinstance(report[field], str) or len(report[field].encode()) > 32768:
            raise ToolOperationError("python_invalid_result")
    report["artifacts"] = []
    receipts = []
    report["missing_output_files"] = [p for p in arguments.output_files if p not in decoded]
    if result["status"] in {"completed", "failed"}:
        for path, content in decoded.items():
            if not await python_authorized(db, run.run_id, task_id):
                raise ToolOperationError("python_not_authorized", status="permission_denied")
            try:
                # Artifact upload failure rolls back its own unit of work. Do not
                # expire the running workflow's ORM state with that rollback.
                factory = async_sessionmaker(db.bind, class_=type(db), expire_on_commit=False)
                async with factory() as artifact_db:
                    artifact_db.info.update(db.info)
                    artifact = await create_run_artifact(
                        db_session=artifact_db, storage=storage, run_id=run.run_id, task_id=task_id,
                        artifact_type="python_output", filename=PurePosixPath(path).name,
                        artifact_content=content, content_type="application/octet-stream",
                    )
            except HTTPException as exc:
                raise ToolOperationError("python_artifact_save_failed", result=report) from exc
            report["artifacts"].append({
                "artifact_id": artifact.artifact_id, "path": path,
                "size_bytes": artifact.size_bytes,
            })
            receipts.append({"artifact_id": artifact.artifact_id, "path": path,
                             "size_bytes": artifact.size_bytes,
                             "content_hash": artifact.content_hash})
    if result.get("retention_receipt_required") and not report["missing_output_files"]:
        try:
            await confirm_python_publication(get_settings().python_runtime_socket, {
                "operation": "confirm_publication", "call_id": record.tool_call_id,
                "scope": {"user_id": run.user_id, "run_id": run.run_id, "task_id": task_id},
                "artifacts": receipts,
            })
            report["publication_confirmed"] = True
        except (OSError, ValueError, TimeoutError):
            # Files are already committed: do not fail the tool or cause code to run twice.
            # The executor remains publication_pending and cannot become a cleanup candidate.
            report["publication_confirmed"] = False
    if result["status"] == "completed" and report["missing_output_files"]:
        raise ToolOperationError("python_output_missing", result=report)
    if result["status"] != "completed":
        raise ToolOperationError(
            "python_" + result["status"], status=result["status"], result=report,
        )
    return report


async def confirm_python_publication(socket_path, receipt):
    """Acknowledge committed files; a lost acknowledgement blocks cleanup, not downloads."""
    writer = None
    try:
        async with asyncio.timeout(10):
            reader, writer = await asyncio.open_unix_connection(socket_path, limit=4096)
            writer.write(json.dumps(receipt).encode() + b"\n")
            await writer.drain()
            response = json.loads(await reader.readline())
            if response != {"confirmed": True}:
                raise ValueError("Publication acknowledgement rejected")
    finally:
        if writer:
            writer.close()
            await writer.wait_closed()


async def execute_authorized_python(db, run_id, task_id, request):
    """Observe persisted cancellation during a long tool call, using independent read sessions."""
    pending = asyncio.create_task(
        execute_python_request(get_settings().python_runtime_socket, request),
    )
    factory = async_sessionmaker(db.bind, class_=type(db), expire_on_commit=False)
    try:
        while not pending.done():
            done, _ = await asyncio.wait({pending}, timeout=0.1)
            if done:
                break
            async with factory() as observer:
                observer.info.update(db.info)
                metadata = (
                    observer.body_metadata_only() if hasattr(observer, "body_metadata_only")
                    else nullcontext()
                )
                with metadata:
                    permitted = await python_authorized(observer, run_id, task_id)
            if not permitted:
                pending.cancel()
                try:
                    await pending
                except asyncio.CancelledError:
                    pass
                raise ToolOperationError("python_cancelled", status="cancelled")
        return await pending
    finally:
        if not pending.done():
            pending.cancel()
            try:
                await asyncio.shield(pending)
            except asyncio.CancelledError:
                pass


async def validate_python_history(db, run_id, task_id, records):
    """Recheck original input grants before previous Python output is sent to a model again."""
    allowed = await conversation_file_ids(db, run_id)
    run = await db.get(LlmRun, run_id, populate_existing=True)
    for record in records:
        if record.tool_name != "run_command" or record.permission_decision != "allowed" or (
            not (record.output_json or {}).get("result")
        ):
            continue
        if not await python_authorized(db, run_id, task_id):
            raise ResourceConflictError("Python history is no longer authorized")
        arguments = PythonArguments.model_validate(record.input_json)
        for identity in arguments.file_ids:
            item = await db.get(UserFile, identity, populate_existing=True)
            if not item or identity not in allowed or item.user_id != run.user_id or (
                item.status != "ready"
            ):
                raise ResourceConflictError("Python input is no longer authorized")
        for identity in arguments.artifact_ids:
            item = await db.get(LlmRunArtifact, identity, populate_existing=True)
            if not item or item.run_id != run_id or item.task_id != task_id:
                raise ResourceConflictError("Python artifact is no longer authorized")
