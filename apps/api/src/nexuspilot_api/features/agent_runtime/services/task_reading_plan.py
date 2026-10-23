"""Persist task-local reading scope in existing Tool Call objects, not a new storage system."""

from pydantic import Field
from sqlalchemy import select

from nexuspilot_api.core.errors import ResourceConflictError, ResourceNotFoundError
from nexuspilot_api.models import LlmModelAttempt, LlmModelToolCall, LlmRun, UserFile
from nexuspilot_api.services.agent_file_tool_service import EmptyArguments, PageArguments
from nexuspilot_api.services.conversation_file_service import conversation_file_ids
from nexuspilot_api.services.user_file_service import read_parsed_file


class ReadingTarget(EmptyArguments):
    """Select zero-based parsed blocks; omitted count means through the parsed document end."""

    file_id: str = Field(min_length=36, max_length=36)
    start_block: int = Field(default=0, ge=0)
    block_count: int | None = Field(default=None, ge=1)


class SaveReadingPlanArguments(EmptyArguments):
    """Declare bounded file targets without accepting user, Task, status or storage identities."""

    targets: list[ReadingTarget] = Field(min_length=1, max_length=10)


class ReadReadingPlanArguments(EmptyArguments):
    """Identify one immutable plan; the execution service supplies its authorized Task."""

    plan_id: str = Field(min_length=36, max_length=36)


async def authorize_target(db, storage, *, run_id, file_id):
    """Resolve only a ready attachment currently accessible to this Run's owner."""
    run = await db.get(LlmRun, run_id, populate_existing=True)
    file = await db.get(UserFile, file_id, populate_existing=True)
    allowed = await conversation_file_ids(db, run_id)
    if (run is None or file is None or file_id not in allowed
            or file.user_id != run.user_id or file.status != "ready"):
        raise ResourceNotFoundError("Reading plan file")
    return file, await read_parsed_file(storage, file)


async def build_reading_plan(db, storage, *, run_id, plan_id, arguments):
    """Freeze explicit parsed-block ranges and existing file versions without reading into input."""
    if len({t.file_id for t in arguments.targets}) != len(arguments.targets):
        raise ResourceConflictError("Reading plan requires unique file targets")
    targets = []
    for target in arguments.targets:
        file, document = await authorize_target(db, storage, run_id=run_id, file_id=target.file_id)
        end = (target.start_block + target.block_count
               if target.block_count is not None else len(document.blocks))
        if target.start_block >= end or end > len(document.blocks):
            raise ResourceConflictError("Reading plan range exceeds parsed document")
        targets.append({"file_id": file.file_id, "source_hash": file.content_hash,
            "parsed_hash": file.parsed_hash, "parser_version": document.parser_version,
            "start_block": target.start_block, "end_block": end,
            "total_parsed_blocks": len(document.blocks)})
    return {"schema_version": "task_reading_plan.v1", "plan_id": plan_id, "targets": targets,
            "scope": "parsed_blocks_only", "model_understanding_verified": False}


async def load_reading_plan(db, storage, *, run_id, task_id, plan_id):
    """Read a committed same-task plan and reauthorize its fixed file versions."""
    record = await db.scalar(select(LlmModelToolCall).join(LlmModelAttempt).where(
        LlmModelToolCall.tool_call_id == plan_id,
        LlmModelToolCall.tool_name == "save_task_reading_plan",
        LlmModelToolCall.status == "completed", LlmModelAttempt.run_id == run_id,
        LlmModelAttempt.task_id == task_id,
    ).execution_options(populate_existing=True))
    if record is None:
        raise ResourceNotFoundError("Task reading plan")
    plan = (record.output_json or {}).get("result")
    if (not isinstance(plan, dict) or plan.get("schema_version") != "task_reading_plan.v1"
            or plan.get("plan_id") != plan_id or not isinstance(plan.get("targets"), list)):
        raise ResourceConflictError("Task reading plan is unavailable")
    for target in plan["targets"]:
        file, document = await authorize_target(
            db, storage, run_id=run_id, file_id=target["file_id"])
        if (target["source_hash"] != file.content_hash or target["parsed_hash"] != file.parsed_hash
                or target["parser_version"] != document.parser_version):
            raise ResourceConflictError("Reading plan file version changed")
    return plan


async def reading_plan_progress(db, storage, *, run_id, task_id, plan_id):
    """Report returned-block coverage for immutable targets, not model comprehension."""
    from nexuspilot_api.features.agent_runtime.services.task_read_coverage import task_read_coverage

    plan = await load_reading_plan(db, storage, run_id=run_id, task_id=task_id, plan_id=plan_id)
    coverage = []
    for target in plan["targets"]:
        result = await task_read_coverage(db, storage, run_id=run_id, task_id=task_id,
            arguments=PageArguments(file_id=target["file_id"]),
            planned_range=(target["start_block"], target["end_block"]))
        coverage.append({"file_id": target["file_id"], **result["planned_range"],
                         "warnings": result["warnings"],
                         "uncovered_pages": result["uncovered_pages"]})
    return {**plan, "coverage": coverage,
            "all_target_blocks_returned": bool(coverage) and all(
                c["unreturned_block_count"] == 0 for c in coverage)}
