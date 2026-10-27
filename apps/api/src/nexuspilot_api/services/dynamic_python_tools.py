"""Task-local Python drafts, sample validation and immutable registered references.

Existing tool input/output bodies persist source and receipts in conversation
storage. No new tables, host exec, package installation or permanent publication.
Validation proves only the supplied sample, not general correctness or safety.
"""

import json
from typing import Any

from pydantic import Field, field_validator, model_validator
from sqlalchemy import select

from nexuspilot_api.core.config import get_settings
from nexuspilot_api.models import LlmModelAttempt, LlmModelToolCall
from nexuspilot_api.services.agent_file_tool_service import (
    EmptyArguments,
    FileToolResult,
    ToolOperationError,
)
from nexuspilot_api.services.python_tool_service import (
    PythonArguments,
    python_authorized,
    run_python,
)
from nexuspilot_api.services.tool_catalog import RegisteredTool
from nexuspilot_api.services.tool_schema import check_schema, validate_value


class DraftArguments(EmptyArguments):
    """Describe run(data) and one required expected example; scope never comes from arguments."""

    description: str = Field(min_length=1, max_length=1000)
    source: str = Field(min_length=1, max_length=24000)
    input_schema: dict
    output_schema: dict
    sample_input: dict
    expected_output: Any

    @field_validator("input_schema", "output_schema")
    @classmethod
    def supported_schema(cls, value):
        """Reject constraints the local validator does not enforce."""
        check_schema(value)
        return value

    @model_validator(mode="after")
    def sample_matches_schema(self):
        """Require an object input and a typed expected result before saving a draft."""
        if self.input_schema["type"] != "object":
            raise ValueError("Input must be an object")
        validate_value(self.input_schema, self.sample_input)
        validate_value(self.output_schema, self.expected_output)
        if len(self.model_dump_json().encode()) > 48000:
            raise ValueError("Draft exceeds capacity")
        return self


class ValidateArguments(EmptyArguments):
    """Refer to a saved immutable draft within this task."""

    draft_id: str = Field(min_length=36, max_length=36)


class RegisterArguments(EmptyArguments):
    """Refer to a successful persisted sample validation, never a model-authored verdict."""

    validation_id: str = Field(min_length=36, max_length=36)


class CallArguments(EmptyArguments):
    """Invoke a registration in this task with JSON inputs; file access remains explicit."""

    registration_id: str = Field(min_length=36, max_length=36)
    arguments: dict


DYNAMIC_ENTRIES = tuple(
    RegisteredTool(
        name=name,
        version="1.0.0",
        description=description,
        arguments=arguments,
        output=FileToolResult,
        required_capabilities=frozenset({"python_execute", "dynamic_python"}),
        risk_level="code_execution",
        execution_type="task_container",
        initially_loaded=True,
        timeout_seconds=None,
        max_result_bytes=65536,
    )
    for name, arguments, description in (
        (
            "create_tool_draft",
            DraftArguments,
            "Save task-local Python source defining run(data). No execution yet. Provide schemas "
            "and one expected example. No network, Shell, pip or credentials.",
        ),
        (
            "validate_tool_draft",
            ValidateArguments,
            "Execute a saved draft's example in the approved Python container. Passing verifies "
            "only that example; failures cannot be registered.",
        ),
        (
            "register_task_tool",
            RegisterArguments,
            "Register a successfully validated draft for this task only. Returns a registration "
            "ID and schemas. Use call_task_tool to invoke it; no permanent publication.",
        ),
        (
            "call_task_tool",
            CallArguments,
            "Invoke an immutable registered Python tool in this task. Validate JSON arguments "
            "and result. Code runs only in the existing restricted Python container.",
        ),
    )
)


async def dynamic_authorized(db, run_id, task_id):
    """Require both explicit feature activation and the existing owner Python grant."""
    return get_settings().dynamic_python_enabled and await python_authorized(db, run_id, task_id)


async def saved_call(db, run_id, task_id, call_id, name):
    """Load committed tool bodies only from the exact current task and Run."""
    record = await db.scalar(
        select(LlmModelToolCall)
        .join(LlmModelAttempt)
        .where(
            LlmModelAttempt.run_id == run_id,
            LlmModelAttempt.task_id == task_id,
            LlmModelToolCall.tool_call_id == call_id,
            LlmModelToolCall.tool_name == name,
            LlmModelToolCall.tool_version == "1.0.0",
            LlmModelToolCall.status == "completed",
        )
    )
    if record is None:
        raise ToolOperationError("task_tool_reference_unavailable", status="permission_denied")
    return record


def program(source, arguments):
    """Build container-only code; capture user prints separately and emit one JSON result."""
    # exec is part of the submitted container program, never executed by the API process.
    return (
        "import json, contextlib, sys\n"
        "namespace = {}\n"
        "with contextlib.redirect_stdout(sys.stderr):\n"
        f" exec({source!r}, namespace)\n"
        f" result = namespace['run'](json.loads({json.dumps(arguments, allow_nan=False)!r}))\n"
        "print(json.dumps(result, ensure_ascii=False, allow_nan=False))\n"
    )


async def execute_dynamic(db, storage, task_id, attempt, record, name, arguments):
    """Advance the persisted lifecycle without trusting caller ownership or a claimed pass."""
    if attempt.task_id != task_id or not await dynamic_authorized(db, attempt.run_id, task_id):
        raise ToolOperationError("dynamic_python_not_allowed", status="permission_denied")
    if name == "create_tool_draft":
        return {
            "draft_id": record.tool_call_id,
            "description": arguments.description,
            "state": "draft",
            "input_schema": arguments.input_schema,
            "output_schema": arguments.output_schema,
        }
    if name == "register_task_tool":
        validation = await saved_call(
            db, attempt.run_id, task_id, arguments.validation_id, "validate_tool_draft"
        )
        draft_id = validation.input_json["draft_id"]
    elif name == "call_task_tool":
        registration = await saved_call(
            db, attempt.run_id, task_id, arguments.registration_id, "register_task_tool"
        )
        validation = await saved_call(
            db,
            attempt.run_id,
            task_id,
            registration.input_json["validation_id"],
            "validate_tool_draft",
        )
        draft_id = validation.input_json["draft_id"]
    else:
        draft_id = arguments.draft_id
    saved = await saved_call(db, attempt.run_id, task_id, draft_id, "create_tool_draft")
    draft = DraftArguments.model_validate(saved.input_json)
    if name == "register_task_tool":
        return {
            "registration_id": record.tool_call_id,
            "draft_id": draft_id,
            "validation_id": arguments.validation_id,
            "state": "registered",
            "input_schema": draft.input_schema,
            "output_schema": draft.output_schema,
        }
    data = draft.sample_input if name == "validate_tool_draft" else arguments.arguments
    try:
        validate_value(draft.input_schema, data)
        command = PythonArguments(stdin=program(draft.source, data))
    except ValueError:
        raise ToolOperationError("task_tool_invalid_arguments") from None
    report = await run_python(db, storage, task_id, attempt, record, command)
    if not await dynamic_authorized(db, attempt.run_id, task_id):
        raise ToolOperationError("dynamic_python_not_allowed", status="permission_denied")
    try:
        if report.get("stdout_truncated"):
            raise ValueError("Truncated result")
        value = json.loads(report["stdout"])
        validate_value(draft.output_schema, value)
        if name == "validate_tool_draft" and value != draft.expected_output:
            raise ValueError("Sample mismatch")
    except (ValueError, KeyError):
        raise ToolOperationError("task_tool_validation_failed") from None
    if name == "validate_tool_draft":
        return {
            "validation_id": record.tool_call_id,
            "draft_id": draft_id,
            "sample_passed": True,
            "value": value,
        }
    return {"registration_id": arguments.registration_id, "value": value}
