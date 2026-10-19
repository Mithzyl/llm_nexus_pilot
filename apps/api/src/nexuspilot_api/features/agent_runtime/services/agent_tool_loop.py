"""Continue a Worker through bounded, audited native read-tool calls across Providers."""

import asyncio
import json
from dataclasses import dataclass, field
from time import monotonic

from nexuspilot_models.contracts import Message, MessageRole
from sqlalchemy import select

from nexuspilot_api.core.errors import InvalidRequestError
from nexuspilot_api.models import LlmModelToolCall, LlmProviderContinuationState, LlmTask
from nexuspilot_api.schemas.responses import ResponsesResult
from nexuspilot_api.services.agent_file_tool_service import AgentFileToolService
from nexuspilot_api.services.tool_context import ToolProgressTracker, allow_tool_request


@dataclass
class ToolLoopResult:
    """Retain the authorized tool dialogue for local output repair, not just the last response."""

    response: ResponsesResult
    messages: list[Message]
    records: list[LlmModelToolCall]


@dataclass
class ToolLoopState:
    """Keep one Agent's dialogue and progress across candidate revisions in this execution."""

    history: list[Message] = field(default_factory=list)
    records: list[LlmModelToolCall] = field(default_factory=list)
    seen: dict = field(default_factory=dict)
    progress: ToolProgressTracker = field(default_factory=ToolProgressTracker)
    loaded_versions: dict[str, str] = field(default_factory=dict)
    deadline: float | None = None
    # Normal loop turns only; format repair uses a separate request-key suffix.
    model_round_count: int = 0


async def run_file_tool_loop(
    service, workflow, node, options, *, state=None, present_result=None, before_model_call=None,
    tool_service=None,
):
    """Reuse dialogue with private-state parent references and an optional scoped catalog."""
    db = service.model_invocation_service.db_session
    tools = tool_service or AgentFileToolService(db, service.model_invocation_service.storage)
    task = await db.get(LlmTask, node.task_id)
    if task is None:
        raise InvalidRequestError("Tool execution requires a persisted Task")
    state = state or ToolLoopState()
    tools.loaded_versions.update(state.loaded_versions)
    history, records, seen, progress = state.history, state.records, state.seen, state.progress
    deadline = monotonic() + min(
        task.timeout_seconds, service.workflow_state.remaining_wall_time_ms(workflow) / 1000
    )
    if state.deadline is not None:
        deadline = min(deadline, state.deadline)
    state.deadline = deadline
    # Native call batches run sequentially: every read observes current ownership.
    while True:
        state.model_round_count += 1
        await service.workflow_state.ensure_workflow_running(workflow)
        await tools.validate_history(workflow.run_id, records)
        if before_model_call is not None:
            await before_model_call(workflow)
        remaining = deadline - monotonic()
        if remaining < 1:
            raise InvalidRequestError("Agent tool task wall-time limit is exhausted")
        definitions = tools.definitions()
        offered_versions = {
            definition.name: tools.loaded_versions[definition.name] for definition in definitions
        }
        response = await service._call_model_round(
            workflow,
            node,
            **{
                **options,
                "timeout_seconds": min(
                    options.get("timeout_seconds") or task.timeout_seconds, remaining
                ),
            },
            round_index=state.model_round_count,
            tool_messages=history,
            tool_definitions=definitions,
            tool_versions=offered_versions,
        )
        if not response.tool_calls:
            return ToolLoopResult(response, history, records)
        if response.finish_reason not in {"tool_calls", "stop"}:
            raise InvalidRequestError("Incomplete model tool call response")
        if len(response.tool_calls) > 8 or len({call.id for call in response.tool_calls}) != len(
            response.tool_calls
        ):
            raise InvalidRequestError("Invalid or excessive tool call batch")
        assistant = Message(
            role=MessageRole.ASSISTANT,
            content=response.output_text or "",
            tool_calls=response.tool_calls,
        )
        continuation = await db.scalar(
            select(LlmProviderContinuationState.parent_attempt_id).where(
                LlmProviderContinuationState.parent_attempt_id == response.id,
            )
        )
        if continuation:
            assistant._provider_continuation_state = (
                await service.model_invocation_service.continuation_store.load_state(
                    parent_attempt_id=continuation,
                    run_id=workflow.run_id,
                    task_id=node.task_id,
                    provider=response.provider,
                    model=response.model,
                )
            )
            assistant._provider_continuation_parent_attempt_id = continuation
        history.append(assistant)
        for call in response.tool_calls:
            await service.workflow_state.ensure_workflow_running(workflow)
            # A call previously denied as unloaded can legitimately succeed after describing it.
            fingerprint = json.dumps([call.name, call.arguments, offered_versions], sort_keys=True)
            if not allow_tool_request(seen, call.name, fingerprint, len(records)):
                raise InvalidRequestError(
                    "Agent repeated an unchanged tool request without progress"
                )
            async with asyncio.timeout(max(0, deadline - monotonic())):
                record = await tools.execute(
                    attempt_id=response.id,
                    call=call,
                    offered_versions=offered_versions,
                )
            records.append(record)
            state.loaded_versions = dict(tools.loaded_versions)
            await service.workflow_state.record_tool_progress(workflow, node)
            if not progress.observe(call.name, call.arguments, record.output_json):
                raise InvalidRequestError("Agent file tools repeated evidence without progress")
            visible_result = present_result(record) if present_result else record.output_json
            history.append(
                Message(
                    role=MessageRole.TOOL,
                    tool_call_id=call.id,
                    tool_name=call.name,
                    content=json.dumps(visible_result, ensure_ascii=False),
                )
            )
