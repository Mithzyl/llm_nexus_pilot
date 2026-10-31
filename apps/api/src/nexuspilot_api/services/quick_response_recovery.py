"""Reuse committed model/tool boundaries under an exclusive quick-response lease."""

from sqlalchemy import select

from nexuspilot_api.core.errors import ResourceConflictError
from nexuspilot_api.models import AttemptStatus, LlmModelAttempt, LlmModelToolCall
from nexuspilot_api.schemas.responses import ResponsesResult
from nexuspilot_api.services.quick_response_submission import read_request


class QuickResponseOutcomeUnknown(ResourceConflictError):
    """Stop recovery when saved facts do not prove a provider/tool operation completed."""


async def completed_response(invocation, payload):
    """Return a completed normalized response, or refuse to repeat an unresolved attempt."""
    db = invocation.db_session
    if not db.info.get("quick_response_claim") or not payload.idempotency_key:
        return None
    attempt = await db.scalar(
        select(LlmModelAttempt).where(LlmModelAttempt.request_key == payload.idempotency_key)
    )
    if attempt is None:
        return None
    if (
        attempt.run_id != payload.run_id
        or attempt.task_id is not None
        or attempt.provider != payload.provider.value
        or attempt.model != payload.model
    ):
        raise ResourceConflictError("Recovered model attempt does not match the request")
    if attempt.status != AttemptStatus.COMPLETED or not attempt.raw_response_uri:
        raise QuickResponseOutcomeUnknown("Previous model result is unresolved; no redispatch")
    raw = await read_request(invocation.storage, attempt.raw_response_uri)
    normalized = raw.get("nexus_completed_response")
    if normalized is None:
        raise QuickResponseOutcomeUnknown(
            "Saved model response lacks a recoverable normalized result"
        )
    return ResponsesResult.model_validate(normalized)


async def completed_tool(tools, run_id, attempt_id, call, versions):
    """Reauthorize a saved terminal tool result; never repeat unknown external side effects."""
    if not tools.db.info.get("quick_response_claim"):
        return None
    record = await tools.db.scalar(
        select(LlmModelToolCall).where(
            LlmModelToolCall.attempt_id == attempt_id,
            LlmModelToolCall.provider_tool_call_id == call.id,
        )
    )
    if record is None:
        return None
    if (
        record.tool_name != call.name
        or record.input_json != call.arguments
        or record.tool_version != versions.get(call.name)
    ):
        raise ResourceConflictError("Recovered tool parameters or version changed")
    if record.status not in {"completed", "failed", "permission_denied"}:
        raise QuickResponseOutcomeUnknown("Previous tool outcome is unresolved; no redispatch")
    await tools.validate_history(run_id, [record])
    if record.tool_name == "describe_tools" and record.status == "completed":
        result = record.output_json["result"]
        tools.loaded_versions[result["name"]] = result["version"]
    return record
