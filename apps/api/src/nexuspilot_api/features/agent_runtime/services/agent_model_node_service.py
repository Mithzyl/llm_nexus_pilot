"""Invoke and validate model-backed Agent workflow nodes."""

import json
from decimal import Decimal
from typing import Any, Protocol

from pydantic import BaseModel, ValidationError

from nexuspilot_api.core.errors import InvalidRequestError
from nexuspilot_api.features.agent_runtime.schemas.agent_workflows import AgentModelBinding
from nexuspilot_api.features.agent_runtime.services.workflow_policy import (
    structured_model_instructions,
)
from nexuspilot_api.models import (
    LlmAgentWorkflowExecution,
    LlmAgentWorkflowNodeExecution,
)
from nexuspilot_api.schemas.responses import ResponsesRequest, ResponsesResult
from nexuspilot_api.services.model_response_service import ModelInvocationService


class AgentModelNodeStatePort(Protocol):
    """Expose the durable workflow guards required around one Provider call."""

    async def ensure_workflow_running(
        self,
        workflow: LlmAgentWorkflowExecution,
    ) -> None:
        """Reject work after a workflow leaves its running state or exhausts its deadline."""

    def remaining_wall_time_ms(self, workflow: LlmAgentWorkflowExecution) -> int:
        """Return the current non-negative execution time allowance."""

    async def reserve_model_call(
        self,
        workflow: LlmAgentWorkflowExecution,
        *,
        reserved_estimated_cost: Decimal | None,
        has_explicit_output_token_bound: bool = True,
    ) -> None:
        """Persist one call and its cost bound, retaining why no bound is available."""

    async def release_model_cost_reservation(
        self,
        workflow: LlmAgentWorkflowExecution,
        *,
        reserved_estimated_cost: Decimal | None,
    ) -> None:
        """Release a completed or aborted Provider call's temporary cost reservation."""


class AgentModelNodeService:
    """Execute typed Provider calls without owning workflow transition decisions."""

    def __init__(
        self,
        *,
        model_invocation_service: ModelInvocationService,
        workflow_state: AgentModelNodeStatePort,
    ) -> None:
        """Bind the shared model gateway and the workflow state guard port."""

        self.model_invocation_service = model_invocation_service
        self.workflow_state = workflow_state

    async def call_typed_model(
        self,
        workflow: LlmAgentWorkflowExecution,
        node: LlmAgentWorkflowNodeExecution,
        *,
        binding: AgentModelBinding,
        role: str,
        purpose: str,
        model_output: type[BaseModel],
        model_input: dict[str, Any],
        timeout_seconds: int | None = None,
    ) -> tuple[Any, ResponsesResult]:
        """Invoke and validate a model node, reserving cost only from an explicit output bound."""

        prompted_json = binding.structured_output_mode == "prompted_json"
        instructions = structured_model_instructions(
            role=role,
            purpose=purpose,
            output_model=model_output,
            prompted_json=prompted_json,
        )
        serialized_model_input = json.dumps(model_input, ensure_ascii=False)
        output_schema = None if prompted_json else model_output.model_json_schema()
        serialized_output_schema = (
            "" if output_schema is None else json.dumps(output_schema, ensure_ascii=False)
        )
        await self.workflow_state.ensure_workflow_running(workflow)
        remaining_wall_time_ms = self.workflow_state.remaining_wall_time_ms(workflow)
        if remaining_wall_time_ms < 1_000:
            raise InvalidRequestError("Agent workflow wall-time limit is exhausted")
        reserved_estimated_cost = (
            self.model_invocation_service.prices.estimate_maximum(
                binding.provider,
                binding.model,
                # UTF-8 bytes form a reproducible upper bound for tokenizer units;
                # the fixed allowance covers message framing added by adapters.
                input_tokens_upper_bound=len(
                    (
                        f"{instructions}\n{serialized_model_input}\n{serialized_output_schema}"
                    ).encode()
                )
                + 256,
                output_tokens_upper_bound=binding.max_output_tokens,
            )
            if binding.max_output_tokens is not None
            else None
        )
        await self.workflow_state.reserve_model_call(
            workflow,
            reserved_estimated_cost=reserved_estimated_cost,
            has_explicit_output_token_bound=binding.max_output_tokens is not None,
        )
        effective_timeout_seconds = min(
            float(binding.timeout_seconds),
            float(timeout_seconds or binding.timeout_seconds),
            remaining_wall_time_ms / 1_000,
        )
        try:
            response = await self.model_invocation_service.generate(
                ResponsesRequest(
                    run_id=workflow.run_id,
                    task_id=node.task_id,
                    provider=binding.provider,
                    model=binding.model,
                    input=serialized_model_input,
                    instructions=instructions,
                    output_schema=output_schema,
                    max_output_tokens=binding.max_output_tokens,
                    timeout_seconds=effective_timeout_seconds,
                    metadata={
                        "workflow_execution_id": workflow.workflow_execution_id,
                        "node_execution_id": node.node_execution_id,
                        "agent_node_key": node.node_key.split(".", maxsplit=1)[0],
                        "agent_node_execution_key": node.node_key,
                        "agent_role": role,
                    },
                    idempotency_key=(
                        f"agent:{workflow.workflow_execution_id}:{node.node_sequence}"
                    ),
                    stream=False,
                )
            )
            await self.workflow_state.ensure_workflow_running(workflow)
            if response.tool_calls:
                raise InvalidRequestError(
                    "Model requested tools but model_only_v1 cannot execute tool calls"
                )
            if response.finish_reason != "stop":
                raise InvalidRequestError(
                    f"Agent model node ended with incomplete finish reason {response.finish_reason}"
                )
            raw_output: object = response.structured_output
            if prompted_json:
                try:
                    raw_output = json.loads(response.output_text or "")
                except json.JSONDecodeError as exc:
                    raise InvalidRequestError(
                        "Prompted JSON Agent node returned invalid JSON"
                    ) from exc
            if not isinstance(raw_output, dict):
                raise InvalidRequestError("Agent model node did not return a structured object")
            try:
                validated = model_output.model_validate(raw_output)
            except ValidationError as exc:
                raise InvalidRequestError(
                    "Agent model node output failed its typed contract"
                ) from exc
            return validated, response
        finally:
            await self.workflow_state.release_model_cost_reservation(
                workflow,
                reserved_estimated_cost=reserved_estimated_cost,
            )
