"""Continue a Worker through bounded, audited native read-tool calls across Providers."""

import asyncio
import json
from dataclasses import dataclass
from time import monotonic

from nexuspilot_models.contracts import Message, MessageRole
from sqlalchemy import select

from nexuspilot_api.core.errors import InvalidRequestError
from nexuspilot_api.models import LlmModelToolCall, LlmProviderContinuationState, LlmTask
from nexuspilot_api.schemas.responses import ResponsesResult
from nexuspilot_api.services.agent_file_tool_service import AgentFileToolService
from nexuspilot_api.services.tool_context import allow_tool_request


@dataclass
class ToolLoopResult:
    """Retain the authorized tool dialogue for local output repair, not just the last response."""

    response: ResponsesResult
    messages: list[Message]
    records: list[LlmModelToolCall]


async def run_file_tool_loop(service, workflow, node, options):
    """Run bounded reads, reserving one format-repair call when at least three are allocated."""
    db = service.model_invocation_service.db_session
    tools = AgentFileToolService(db, service.model_invocation_service.storage)
    task = await db.get(LlmTask, node.task_id)
    if task is None:
        raise InvalidRequestError("Tool execution requires a persisted Task")
    history = []
    records = []
    seen = {}
    deadline = monotonic() + min(
        task.timeout_seconds, service.workflow_state.remaining_wall_time_ms(workflow) / 1000
    )
    max_calls = options["model_input"]["max_model_calls"]
    typed_final_round = (
        node.node_key == "independent_review" or workflow.workflow_version != "1.0.0"
    )
    # A two-call legacy plan can only read then answer. Larger plans reserve the last
    # call for validated-output repair instead of spending every call on discovery/reads.
    answer_round_limit = max_calls - 1 if typed_final_round and max_calls >= 3 else max_calls
    # Native call batches run sequentially: every read observes current ownership.
    for round_index in range(1, answer_round_limit + 1):
        await service.workflow_state.ensure_workflow_running(workflow)
        await tools.validate_history(workflow.run_id, records)
        remaining = deadline - monotonic()
        if remaining < 1:
            raise InvalidRequestError("Agent tool task wall-time limit is exhausted")
        definitions = tools.definitions()
        if typed_final_round and round_index == answer_round_limit:
            definitions = []
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
            round_index=round_index,
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
            await service.workflow_state.record_tool_progress(workflow, node)
            history.append(
                Message(
                    role=MessageRole.TOOL,
                    tool_call_id=call.id,
                    tool_name=call.name,
                    content=json.dumps(record.output_json, ensure_ascii=False),
                )
            )
    raise InvalidRequestError("Agent task model-call budget is exhausted")
