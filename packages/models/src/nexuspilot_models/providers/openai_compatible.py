"""Configurable OpenAI Chat Completions compatible provider implementation."""

import json
import time
from collections.abc import AsyncIterator
from typing import Any

from nexuspilot_models.contracts import (
    MessageRole,
    ModelRequest,
    ModelResponse,
    ProviderContinuationKind,
    ProviderContinuationState,
    ProviderName,
    ReasoningBlockStatus,
    ReasoningPresentation,
    ReasoningPresentationKind,
    StreamEvent,
    StreamEventType,
    ToolCall,
)
from nexuspilot_models.errors import ModelProviderError
from nexuspilot_models.diagnostics import provider_error_diagnostic
from nexuspilot_models.providers.common import (
    parse_json_arguments,
    validate_structured_response,
    preserve_stream_usage,
)
from nexuspilot_models.transport import HttpTransport


class OpenAICompatibleChatProvider:
    """Adapt OpenAI-style Chat Completions APIs through configurable endpoints and headers."""

    def __init__(
        self,
        *,
        name: ProviderName,
        transport: HttpTransport,
        base_url: str,
        api_key: str,
        supports_json_schema: bool = True,
        supports_json_object_output: bool = False,
        supports_reasoning_configuration: bool = False,
        requires_done_marker: bool = False,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        """Configure one OpenAI-compatible provider without coupling it to FastAPI or storage."""

        self.name = name
        self.transport = transport
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.supports_json_schema = supports_json_schema
        self.supports_json_object_output = supports_json_object_output
        self.supports_reasoning_configuration = supports_reasoning_configuration
        self.requires_done_marker = requires_done_marker
        self.extra_headers = extra_headers or {}

    async def generate(self, request: ModelRequest) -> ModelResponse:
        """Call a compatible chat endpoint and normalize text, tools, usage, and finish reason."""

        self._validate_request(request)
        started = time.perf_counter()
        result = await self.transport.post_json(
            f"{self.base_url}/chat/completions",
            headers=self._headers(),
            payload=self._build_payload(request, stream=False),
            timeout_seconds=request.timeout_seconds,
        )
        return self._parse_response(
            result.payload,
            request,
            latency_ms=self._elapsed_ms(started),
            attempts=result.attempts,
        )

    @preserve_stream_usage
    async def stream(self, request: ModelRequest) -> AsyncIterator[StreamEvent]:
        """Normalize Chat SSE; reject errors/refusals and require an observed finish reason."""

        self._validate_request(request)
        started = time.perf_counter()
        sequence = 1
        text_parts: list[str] = []
        reasoning_parts: list[str] = []
        reasoning_started = False
        tool_parts: dict[int, dict[str, str]] = {}
        finish_reason = None
        usage: dict[str, Any] = {}
        request_id: str | None = None
        received_done_marker = False
        async with self.transport.open_sse(
            f"{self.base_url}/chat/completions",
            headers=self._headers(),
            payload=self._build_payload(request, stream=True),
            timeout_seconds=request.timeout_seconds,
        ) as (messages, _headers, attempts):
            yield StreamEvent(type=StreamEventType.STARTED, sequence=sequence)
            sequence += 1
            async for message in messages:
                if message.data == "[DONE]":
                    received_done_marker = True
                    break
                try:
                    chunk = json.loads(message.data)
                except json.JSONDecodeError as exc:
                    raise ModelProviderError(
                        "response_parse_error",
                        "Compatible provider returned invalid stream JSON.",
                        transport_attempts=attempts,
                    ) from exc
                if not isinstance(chunk, dict):
                    raise ModelProviderError(
                        "response_parse_error",
                        "Chat stream chunk must be an object.",
                        transport_attempts=attempts,
                    )
                self._check_provider_error(chunk, attempts)
                request_id = chunk.get("id") or request_id
                if isinstance(chunk.get("usage"), dict):
                    usage = chunk["usage"]
                    yield StreamEvent(
                        type=StreamEventType.USAGE,
                        sequence=sequence,
                        data=self._normalize_usage(usage),
                    )
                    sequence += 1
                choices = chunk.get("choices") or []
                if not choices:
                    continue
                choice = choices[0]
                finish_reason = choice.get("finish_reason") or finish_reason
                delta = choice.get("delta") or {}
                self._check_refusal(delta, attempts)
                reasoning_content = delta.get("reasoning_content")
                if isinstance(reasoning_content, str) and reasoning_content:
                    if not reasoning_started:
                        reasoning_started = True
                        yield StreamEvent(
                            type=StreamEventType.REASONING_STARTED,
                            sequence=sequence,
                            data={
                                "block_id": "reasoning-0",
                                "kind": ReasoningPresentationKind.RAW.value,
                            },
                        )
                        sequence += 1
                    reasoning_parts.append(reasoning_content)
                    yield StreamEvent(
                        type=StreamEventType.REASONING_RAW_DELTA,
                        sequence=sequence,
                        data={"block_id": "reasoning-0", "delta": reasoning_content},
                    )
                    sequence += 1
                content = delta.get("content")
                if isinstance(content, str) and content:
                    text_parts.append(content)
                    yield StreamEvent(
                        type=StreamEventType.TEXT_DELTA,
                        sequence=sequence,
                        data={"delta": content},
                    )
                    sequence += 1
                for tool_delta in delta.get("tool_calls") or []:
                    index = int(tool_delta.get("index", 0))
                    current = tool_parts.setdefault(
                        index, {"id": "", "name": "", "arguments": ""}
                    )
                    current["id"] = tool_delta.get("id") or current["id"]
                    function = tool_delta.get("function") or {}
                    current["name"] += function.get("name") or ""
                    argument_delta = function.get("arguments") or ""
                    current["arguments"] += argument_delta
                    yield StreamEvent(
                        type=StreamEventType.TOOL_CALL_DELTA,
                        sequence=sequence,
                        data={
                            "index": index,
                            "id": current["id"],
                            "name": current["name"],
                            "arguments_delta": argument_delta,
                        },
                    )
                    sequence += 1

        if self.requires_done_marker and not received_done_marker:
            raise ModelProviderError(
                "response_parse_error",
                "Compatible provider stream ended without the required [DONE] marker.",
                transport_attempts=attempts,
            )

        text = "".join(text_parts) or None
        self._check_completion(finish_reason, text, bool(tool_parts), attempts)
        reasoning_text = "".join(reasoning_parts) or None
        reasoning_tokens = self._reasoning_tokens(usage)
        reasoning_blocks = (
            [
                ReasoningPresentation(
                    kind=ReasoningPresentationKind.RAW,
                    block_id="reasoning-0",
                    status=ReasoningBlockStatus.COMPLETED,
                    text=reasoning_text,
                    reasoning_tokens=reasoning_tokens,
                )
            ]
            if reasoning_text
            else []
        )
        provider_continuation_state = (
            ProviderContinuationState(
                kind=ProviderContinuationKind.DEEPSEEK_RAW_REASONING,
                provider_response_id=request_id,
                raw_reasoning_for_tool_continuation=reasoning_text,
                tool_call_ids=[
                    value["id"] or f"tool-{index}"
                    for index, value in sorted(tool_parts.items())
                ],
                tool_calls=[
                    {
                        "id": value["id"] or f"tool-{index}",
                        "name": value["name"],
                        "arguments": value["arguments"],
                    }
                    for index, value in sorted(tool_parts.items())
                ],
            )
            if self.name is ProviderName.DEEPSEEK and reasoning_text and tool_parts
            else None
        )
        response = ModelResponse(
            text=text,
            tool_calls=[
                ToolCall(
                    id=value["id"] or f"tool-{index}",
                    name=value["name"],
                    arguments=parse_json_arguments(
                        value["arguments"], context="tool call"
                    ),
                )
                for index, value in sorted(tool_parts.items())
            ],
            finish_reason=self._finish_reason(finish_reason),
            input_tokens=usage.get("prompt_tokens"),
            output_tokens=usage.get("completion_tokens"),
            cached_tokens=self._cached_tokens(usage),
            cache_miss_tokens=self._cache_miss_tokens(usage),
            reasoning_tokens=reasoning_tokens,
            reasoning_blocks=reasoning_blocks,
            provider_continuation_state=provider_continuation_state,
            latency_ms=self._elapsed_ms(started),
            provider_request_id=request_id,
            raw_response={
                "record_kind": "normalized_stream_aggregate",
                "streamed": True,
                "text": text,
                "reasoning_content": reasoning_text,
                "tool_calls": list(tool_parts.values()),
                "finish_reason": finish_reason,
                "usage": usage,
            },
            transport_attempts=attempts,
        )
        response = validate_structured_response(response, request)
        if reasoning_blocks:
            yield StreamEvent(
                type=StreamEventType.REASONING_COMPLETED,
                sequence=sequence,
                data={"block": reasoning_blocks[0].model_dump(mode="json")},
            )
            sequence += 1
        yield StreamEvent(
            type=StreamEventType.COMPLETED,
            sequence=sequence,
            data={"response": response.model_dump(mode="json")},
            private_data={
                "provider_continuation_state": provider_continuation_state.model_dump(
                    mode="json"
                )
            }
            if provider_continuation_state
            else {},
        )

    def _build_payload(self, request: ModelRequest, *, stream: bool) -> dict[str, Any]:
        """Encode ordered tool turns and supply JSON guidance only when system policy is absent."""

        messages: list[dict[str, Any]] = []
        system_instruction = request.system_instruction
        if request.json_object_output and not (system_instruction or "").strip():
            # Native JSON mode alone has returned malformed values in real
            # completions without system guidance. Keep explicit caller policy
            # unchanged, but make the requested format unambiguous when absent.
            system_instruction = (
                "Output one valid JSON object matching the user's requested structure. "
                "Do not include Markdown fences or text outside the JSON."
            )
        if system_instruction:
            messages.append({"role": "system", "content": system_instruction})
        continuation_inserted = False
        for message in request.messages:
            continuation_state = request.provider_continuation_state
            if (
                message.role is MessageRole.TOOL
                and not any(item.tool_calls for item in request.messages)
                and not continuation_inserted
                and continuation_state
                and continuation_state.kind
                is ProviderContinuationKind.DEEPSEEK_RAW_REASONING
            ):
                messages.append(
                    {
                        "role": "assistant",
                        "content": None,
                        "reasoning_content": (
                            continuation_state.raw_reasoning_for_tool_continuation
                        ),
                        "tool_calls": [
                            {
                                "id": tool_call.get("id"),
                                "type": "function",
                                "function": {
                                    "name": tool_call.get("name"),
                                    "arguments": tool_call.get("arguments", "{}"),
                                },
                            }
                            for tool_call in continuation_state.tool_calls
                        ],
                    }
                )
                continuation_inserted = True
            item: dict[str, Any] = {
                "role": message.role.value,
                "content": message.content,
            }
            if message.role is MessageRole.TOOL:
                item["tool_call_id"] = message.tool_call_id
            if message.tool_calls:
                item["content"] = message.content or None
                item["tool_calls"] = [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {
                            "name": call.name,
                            "arguments": json.dumps(call.arguments),
                        },
                    }
                    for call in message.tool_calls
                ]
                state = message._provider_continuation_state
                if (
                    state
                    and state.kind is ProviderContinuationKind.DEEPSEEK_RAW_REASONING
                ):
                    item["reasoning_content"] = (
                        state.raw_reasoning_for_tool_continuation
                    )
            messages.append(item)
        payload: dict[str, Any] = {
            "model": request.model,
            "messages": messages,
            "stream": stream,
        }
        if stream:
            payload["stream_options"] = {"include_usage": True}
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.max_output_tokens is not None:
            payload["max_tokens"] = request.max_output_tokens
        if request.tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.input_schema,
                        **({"strict": True} if tool.strict else {}),
                    },
                }
                for tool in request.tools
            ]
        if request.json_object_output:
            payload["response_format"] = {"type": "json_object"}
        elif request.output_schema:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "nexuspilot_output",
                    "strict": True,
                    "schema": request.output_schema,
                },
            }
        return payload

    def _parse_response(
        self,
        payload: dict[str, Any],
        request: ModelRequest,
        *,
        latency_ms: int,
        attempts: list,
    ) -> ModelResponse:
        """Validate terminal status and normalize a non-streaming chat response."""

        self._check_provider_error(payload, attempts)
        choices = payload.get("choices") or []
        if not choices:
            raise ModelProviderError(
                "response_parse_error",
                "Compatible provider response contains no choices.",
                transport_attempts=attempts,
            )
        choice = choices[0]
        message = choice.get("message") or {}
        self._check_refusal(message, attempts)
        text = message.get("content")
        self._check_completion(
            choice.get("finish_reason"), text, bool(message.get("tool_calls")), attempts
        )
        reasoning_text = message.get("reasoning_content")
        if not isinstance(reasoning_text, str) or not reasoning_text:
            reasoning_text = None
        tool_calls = [
            ToolCall(
                id=item.get("id") or f"tool-{index}",
                name=(item.get("function") or {}).get("name", ""),
                arguments=parse_json_arguments(
                    (item.get("function") or {}).get("arguments", "{}"),
                    context="tool call",
                ),
            )
            for index, item in enumerate(message.get("tool_calls") or [])
        ]
        usage = payload.get("usage") or {}
        reasoning_tokens = self._reasoning_tokens(usage)
        reasoning_blocks = (
            [
                ReasoningPresentation(
                    kind=ReasoningPresentationKind.RAW,
                    block_id="reasoning-0",
                    status=ReasoningBlockStatus.COMPLETED,
                    text=reasoning_text,
                    reasoning_tokens=reasoning_tokens,
                )
            ]
            if reasoning_text
            else []
        )
        provider_continuation_state = (
            ProviderContinuationState(
                kind=ProviderContinuationKind.DEEPSEEK_RAW_REASONING,
                provider_response_id=payload.get("id"),
                raw_reasoning_for_tool_continuation=reasoning_text,
                tool_call_ids=[tool_call.id for tool_call in tool_calls],
                tool_calls=[
                    {
                        "id": tool_call.id,
                        "name": tool_call.name,
                        "arguments": json.dumps(
                            tool_call.arguments, ensure_ascii=False
                        ),
                    }
                    for tool_call in tool_calls
                ],
            )
            if self.name is ProviderName.DEEPSEEK and reasoning_text and tool_calls
            else None
        )
        response = ModelResponse(
            text=text,
            tool_calls=tool_calls,
            finish_reason=self._finish_reason(choice.get("finish_reason")),
            input_tokens=usage.get("prompt_tokens"),
            output_tokens=usage.get("completion_tokens"),
            cached_tokens=self._cached_tokens(usage),
            cache_miss_tokens=self._cache_miss_tokens(usage),
            reasoning_tokens=reasoning_tokens,
            reasoning_blocks=reasoning_blocks,
            provider_continuation_state=provider_continuation_state,
            latency_ms=latency_ms,
            provider_request_id=payload.get("id"),
            raw_response=payload,
            transport_attempts=attempts,
        )

        return validate_structured_response(response, request)

    def _headers(self) -> dict[str, str]:
        """Return authentication and content headers for a compatible endpoint."""

        headers = {"Content-Type": "application/json", **self.extra_headers}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _validate_request(self, request: ModelRequest) -> None:
        """Validate routing and thinking tool state; disabled thinking needs no private state."""

        if any(message.attachments for message in request.messages):
            raise ModelProviderError(
                "unsupported_capability",
                "This codec does not support native file input.",
            )
        if request.provider is not self.name:
            raise ModelProviderError(
                "invalid_request",
                f"Request provider '{request.provider}' does not match adapter '{self.name}'.",
            )
        if request.output_schema and not self.supports_json_schema:
            raise ModelProviderError(
                "unsupported_capability",
                f"Provider '{self.name.value}' does not declare JSON Schema output support.",
            )
        if request.json_object_output and not self.supports_json_object_output:
            raise ModelProviderError(
                "unsupported_capability",
                f"Provider '{self.name.value}' does not declare JSON object output support.",
            )
        if request.reasoning and not self.supports_reasoning_configuration:
            raise ModelProviderError(
                "unsupported_capability",
                f"Provider '{self.name.value}' does not declare reasoning configuration support.",
            )
        if (
            self.name is ProviderName.DEEPSEEK
            and (request.reasoning is None or request.reasoning.enabled)
            and any(message.role is MessageRole.TOOL for message in request.messages)
        ):
            # Match _build_payload: explicit assistant tool turns carry their own
            # state. The legacy request-level state is used only when the payload
            # builder must reconstruct an absent assistant tool-call message.
            continuation_states = [
                message._provider_continuation_state
                for message in request.messages
                if message.tool_calls
            ] or [request.provider_continuation_state]
            if any(
                state is None
                or state.kind is not ProviderContinuationKind.DEEPSEEK_RAW_REASONING
                for state in continuation_states
            ):
                raise ModelProviderError(
                    "invalid_request",
                    "DeepSeek tool continuation requires backend-preserved reasoning state.",
                )

    @staticmethod
    def _check_provider_error(payload: dict[str, Any], attempts: list) -> None:
        """Reject in-band errors with fixed public text and allowlisted private diagnostics."""
        if payload.get("error") is not None:
            raise ModelProviderError(
                "provider_unavailable",
                "Chat provider returned an error envelope.",
                transport_attempts=attempts,
                raw_error=provider_error_diagnostic(payload, http_status=200),
            )

    @staticmethod
    def _check_refusal(message: dict[str, Any], attempts: list) -> None:
        """Keep refusal distinct from empty output and never attempt JSON repair on it."""
        if message.get("refusal"):
            raise ModelProviderError(
                "content_filter",
                "Chat provider refused the request.",
                transport_attempts=attempts,
            )

    @staticmethod
    def _check_completion(
        reason: str | None, text: str | None, has_tools: bool, attempts: list
    ) -> None:
        """Require a real terminal reason; preserve partial length output, reject empty success."""
        if not reason:
            raise ModelProviderError(
                "response_parse_error",
                "Chat response has no terminal finish reason.",
                transport_attempts=attempts,
            )
        if reason == "content_filter":
            raise ModelProviderError(
                "content_filter",
                "Chat provider filtered the response.",
                transport_attempts=attempts,
            )
        if reason == "insufficient_system_resource":
            raise ModelProviderError(
                "provider_unavailable",
                "Chat provider exhausted execution resources.",
                transport_attempts=attempts,
            )
        if not text and not has_tools:
            raise ModelProviderError(
                "response_parse_error",
                "Chat response contains no text or tool output.",
                transport_attempts=attempts,
            )

    def _normalize_usage(self, usage: dict[str, Any]) -> dict[str, Any]:
        """Map compatible usage fields to public stream accounting names."""

        return {
            "input_tokens": usage.get("prompt_tokens"),
            "output_tokens": usage.get("completion_tokens"),
            "cached_tokens": self._cached_tokens(usage),
            "cache_miss_tokens": self._cache_miss_tokens(usage),
            "reasoning_tokens": self._reasoning_tokens(usage),
        }

    def _cached_tokens(self, usage: dict[str, Any]) -> int | None:
        """Read cache usage from OpenAI-style or DeepSeek-style usage objects."""

        details = usage.get("prompt_tokens_details") or {}
        detailed_cached_tokens = details.get("cached_tokens")
        if isinstance(detailed_cached_tokens, int):
            return detailed_cached_tokens
        compatible_cached_tokens = usage.get("prompt_cache_hit_tokens")
        return (
            compatible_cached_tokens
            if isinstance(compatible_cached_tokens, int)
            else None
        )

    def _cache_miss_tokens(self, usage: dict[str, Any]) -> int | None:
        """Read an explicit miss count or derive it from the normalized prompt total."""

        explicit_miss = usage.get("prompt_cache_miss_tokens")
        if isinstance(explicit_miss, int):
            return explicit_miss
        input_tokens = usage.get("prompt_tokens")
        cached_tokens = self._cached_tokens(usage)
        if isinstance(input_tokens, int) and isinstance(cached_tokens, int):
            return max(0, input_tokens - cached_tokens)
        return None

    def _reasoning_tokens(self, usage: dict[str, Any]) -> int | None:
        """Read provider-reported reasoning-token usage without implying visible text."""

        details = usage.get("completion_tokens_details") or {}
        reasoning_tokens = details.get("reasoning_tokens")
        return reasoning_tokens if isinstance(reasoning_tokens, int) else None

    def _finish_reason(self, value: str | None) -> str:
        """Map compatible stop values to stable platform reasons."""

        return {
            "stop": "stop",
            "length": "length",
            "tool_calls": "tool_calls",
            "content_filter": "content_filter",
            "insufficient_system_resource": "provider_error",
        }.get(value or "stop", value or "stop")

    def _elapsed_ms(self, started: float) -> int:
        """Return total provider-call latency using a monotonic clock."""

        return max(0, int((time.perf_counter() - started) * 1000))
