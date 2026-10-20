"""Anthropic Messages API codec implemented on the shared transport."""

import json
import time
from collections.abc import AsyncIterator
from typing import Any

from nexuspilot_models.contracts import (
    MAX_PROVIDER_OUTPUT_TOKENS,
    MessageRole,
    ModelRequest,
    ModelResponse,
    ProviderContinuationKind,
    ProviderContinuationState,
    ProviderName,
    StreamEvent,
    StreamEventType,
    ToolCall,
)
from nexuspilot_models.errors import ModelProviderError
from nexuspilot_models.diagnostics import provider_error_diagnostic
from nexuspilot_models.providers.common import (
    native_content_blocks,
    parse_json_arguments,
    validate_structured_response,
    preserve_stream_usage,
)
from nexuspilot_models.transport import HttpTransport


class AnthropicMessagesProvider:
    """Translate the platform contract to and from Anthropic Messages objects."""

    name = ProviderName.ANTHROPIC

    def __init__(
        self, transport: HttpTransport, *, base_url: str, api_key: str
    ) -> None:
        """Configure the Anthropic endpoint and credential without owning transport shutdown."""

        self.transport = transport
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    async def generate(self, request: ModelRequest) -> ModelResponse:
        """Call Anthropic Messages and normalize content blocks, usage, and stop reason."""

        self._validate_request(request)
        started = time.perf_counter()
        result = await self.transport.post_json(
            f"{self.base_url}/messages",
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
        """Observe Messages stop signals privately while requiring both for completion."""

        self._validate_request(request)
        started = time.perf_counter()
        sequence = 1
        text_parts: list[str] = []
        tools: dict[int, dict[str, str]] = {}
        input_tokens: int | None = None
        cached_tokens: int | None = None
        cache_write_tokens: int | None = None
        cache_miss_tokens: int | None = None
        output_tokens: int | None = None
        stop_reason = None
        received_message_stop = False
        protocol_evidence = request._stream_protocol_evidence
        protocol_evidence.clear()
        protocol_evidence.update({
            "protocol": "messages_sse",
            "stop_reason": None,
            "message_stop_received": False,
        })
        request_id: str | None = None
        async with self.transport.open_sse(
            f"{self.base_url}/messages",
            headers=self._headers(),
            payload=self._build_payload(request, stream=True),
            timeout_seconds=request.timeout_seconds,
        ) as (messages, _headers, attempts):
            yield StreamEvent(type=StreamEventType.STARTED, sequence=sequence)
            sequence += 1
            async for message in messages:
                try:
                    event = json.loads(message.data)
                except json.JSONDecodeError as exc:
                    raise ModelProviderError(
                        "response_parse_error",
                        "Anthropic returned invalid stream JSON.",
                        transport_attempts=attempts,
                    ) from exc
                event_type = event.get("type")
                if event_type == "message_start":
                    native_message = event.get("message") or {}
                    request_id = native_message.get("id")
                    normalized_usage = self._normalize_usage(
                        native_message.get("usage") or {}
                    )
                    input_tokens = normalized_usage["input_tokens"]
                    cached_tokens = normalized_usage["cached_tokens"]
                    cache_write_tokens = normalized_usage["cache_write_tokens"]
                    cache_miss_tokens = normalized_usage["cache_miss_tokens"]
                    yield StreamEvent(
                        type=StreamEventType.USAGE,
                        sequence=sequence,
                        data=normalized_usage,
                    )
                    sequence += 1
                elif event_type == "content_block_start":
                    block = event.get("content_block") or {}
                    if block.get("type") == "tool_use":
                        tools[int(event.get("index", 0))] = {
                            "id": block.get("id", ""),
                            "name": block.get("name", ""),
                            "arguments": "",
                        }
                elif event_type == "content_block_delta":
                    delta = event.get("delta") or {}
                    if delta.get("type") == "text_delta":
                        value = delta.get("text", "")
                        text_parts.append(value)
                        yield StreamEvent(
                            type=StreamEventType.TEXT_DELTA,
                            sequence=sequence,
                            data={"delta": value},
                        )
                        sequence += 1
                    elif delta.get("type") == "input_json_delta":
                        index = int(event.get("index", 0))
                        current = tools.setdefault(
                            index, {"id": f"tool-{index}", "name": "", "arguments": ""}
                        )
                        value = delta.get("partial_json", "")
                        current["arguments"] += value
                        yield StreamEvent(
                            type=StreamEventType.TOOL_CALL_DELTA,
                            sequence=sequence,
                            data={
                                "index": index,
                                "id": current["id"],
                                "name": current["name"],
                                "arguments_delta": value,
                            },
                        )
                        sequence += 1
                elif event_type == "message_delta":
                    stop_reason = (event.get("delta") or {}).get(
                        "stop_reason"
                    ) or stop_reason
                    if stop_reason:
                        protocol_evidence["stop_reason"] = (
                            stop_reason if isinstance(stop_reason, str) and stop_reason in {
                                "end_turn", "max_tokens", "tool_use", "refusal",
                                "model_context_window_exceeded", "stop_sequence", "pause_turn",
                            } else "unknown"
                        )
                    usage = event.get("usage") or {}
                    output_tokens = usage.get("output_tokens", output_tokens)
                    yield StreamEvent(
                        type=StreamEventType.USAGE,
                        sequence=sequence,
                        data={
                            "input_tokens": input_tokens,
                            "output_tokens": output_tokens,
                            "cached_tokens": cached_tokens,
                            "cache_write_tokens": cache_write_tokens,
                            "cache_miss_tokens": cache_miss_tokens,
                        },
                    )
                    sequence += 1
                elif event_type == "message_stop":
                    received_message_stop = True
                    protocol_evidence["message_stop_received"] = True
                elif event_type == "error":
                    error = event.get("error") or {}
                    raise ModelProviderError(
                        "provider_unavailable",
                        "Anthropic stream failed.",
                        retryable=isinstance(error, dict)
                        and error.get("type") == "overloaded_error",
                        raw_error=provider_error_diagnostic(
                            {"error": error}, http_status=200
                        ),
                        transport_attempts=attempts,
                    )

        if not received_message_stop or not stop_reason:
            raise ModelProviderError(
                "response_parse_error",
                "Anthropic stream ended without message_stop and stop_reason.",
                transport_attempts=attempts,
            )
        text = "".join(text_parts) or None
        response = ModelResponse(
            text=text,
            tool_calls=[
                ToolCall(
                    id=value["id"],
                    name=value["name"],
                    arguments=parse_json_arguments(
                        value["arguments"], context="tool call"
                    ),
                )
                for _, value in sorted(tools.items())
            ],
            finish_reason=self._finish_reason(stop_reason),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_tokens=cached_tokens,
            cache_write_tokens=cache_write_tokens,
            cache_miss_tokens=cache_miss_tokens,
            latency_ms=self._elapsed_ms(started),
            provider_request_id=request_id,
            raw_response={
                "streamed": True,
                "text": text,
                "tool_calls": list(tools.values()),
                "stop_reason": stop_reason,
                "usage": {
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "cached_tokens": cached_tokens,
                    "cache_write_tokens": cache_write_tokens,
                    "cache_miss_tokens": cache_miss_tokens,
                },
            },
            transport_attempts=attempts,
        )
        response = validate_structured_response(response, request)
        yield StreamEvent(
            type=StreamEventType.COMPLETED,
            sequence=sequence,
            data={"response": response.model_dump(mode="json")},
        )

    def _build_payload(self, request: ModelRequest, *, stream: bool) -> dict[str, Any]:
        """Translate complete tool exchanges, retaining signed assistant content unchanged."""

        messages: list[dict[str, Any]] = []
        for message in request.messages:
            if message.role is MessageRole.TOOL:
                messages.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": message.tool_call_id,
                                "content": message.content,
                            }
                        ],
                    }
                )
            else:
                if message.tool_calls:
                    state = message._provider_continuation_state
                    content = (
                        state.signed_content_blocks
                        if state
                        and state.kind is ProviderContinuationKind.ANTHROPIC_CONTENT
                        else (
                            [{"type": "text", "text": message.content}]
                            if message.content
                            else []
                        )
                        + [
                            {
                                "type": "tool_use",
                                "id": call.id,
                                "name": call.name,
                                "input": call.arguments,
                            }
                            for call in message.tool_calls
                        ]
                    )
                    messages.append({"role": "assistant", "content": content})
                    continue
                messages.append(
                    {
                        "role": message.role.value,
                        "content": native_content_blocks(message, self.name)
                        if message.attachments
                        else message.content,
                    }
                )
        # Parallel results from one assistant turn belong to a single user message.
        merged = []
        for item in messages:
            if (
                merged
                and item["role"] == merged[-1]["role"] == "user"
                and isinstance(item["content"], list)
                and isinstance(merged[-1]["content"], list)
            ):
                merged[-1]["content"].extend(item["content"])
            else:
                merged.append(item)
        payload: dict[str, Any] = {
            "model": request.model,
            "messages": merged,
            "max_tokens": request.max_output_tokens or MAX_PROVIDER_OUTPUT_TOKENS,
            "stream": stream,
        }
        if request.system_instruction:
            payload["system"] = request.system_instruction
        if request.temperature is not None:
            payload["temperature"] = min(request.temperature, 1)
        if request.tools:
            payload["tools"] = [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "input_schema": tool.input_schema,
                    **({"strict": True} if tool.strict else {}),
                }
                for tool in request.tools
            ]
        if request.output_schema:
            payload["output_config"] = {
                "format": {
                    "type": "json_schema",
                    "schema": request.output_schema,
                }
            }
        if request.reasoning:
            payload["thinking"] = {
                "type": "adaptive" if request.reasoning.enabled else "disabled"
            }
            if request.reasoning.enabled:
                payload.setdefault("output_config", {})["effort"] = (
                    self._provider_reasoning_effort(request)
                )
        return payload

    def _parse_response(
        self,
        payload: dict[str, Any],
        request: ModelRequest,
        *,
        latency_ms: int,
        attempts: list,
    ) -> ModelResponse:
        """Normalize visible text and preserve complete signed tool turns as private state."""

        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        for block in payload.get("content") or []:
            if block.get("type") == "text":
                text_parts.append(block.get("text", ""))
            elif block.get("type") == "tool_use":
                tool_calls.append(
                    ToolCall(
                        id=block.get("id", ""),
                        name=block.get("name", ""),
                        arguments=parse_json_arguments(
                            block.get("input", {}), context="tool call"
                        ),
                    )
                )
        text = "".join(text_parts) or None
        usage = payload.get("usage") or {}
        normalized_usage = self._normalize_usage(usage)
        response = ModelResponse(
            text=text,
            tool_calls=tool_calls,
            provider_continuation_state=(
                ProviderContinuationState(
                    kind=ProviderContinuationKind.ANTHROPIC_CONTENT,
                    signed_content_blocks=payload.get("content") or [],
                )
                if tool_calls
                else None
            ),
            finish_reason=self._finish_reason(payload.get("stop_reason")),
            input_tokens=normalized_usage["input_tokens"],
            output_tokens=usage.get("output_tokens"),
            cached_tokens=normalized_usage["cached_tokens"],
            cache_write_tokens=normalized_usage["cache_write_tokens"],
            cache_miss_tokens=normalized_usage["cache_miss_tokens"],
            latency_ms=latency_ms,
            provider_request_id=payload.get("id"),
            raw_response=payload,
            transport_attempts=attempts,
        )

        return validate_structured_response(response, request)

    def _normalize_usage(self, usage: dict[str, Any]) -> dict[str, int | None]:
        """Convert Anthropic's disjoint cache counters to the platform's inclusive total."""

        ordinary_input = usage.get("input_tokens")
        cached_input = usage.get("cache_read_input_tokens")
        cache_write_input = usage.get("cache_creation_input_tokens")
        numeric_parts = [
            value
            for value in (ordinary_input, cached_input, cache_write_input)
            if isinstance(value, int)
        ]
        total_input = sum(numeric_parts) if numeric_parts else None
        cache_miss = (
            (ordinary_input if isinstance(ordinary_input, int) else 0)
            + (cache_write_input if isinstance(cache_write_input, int) else 0)
            if numeric_parts
            else None
        )
        return {
            "input_tokens": total_input,
            "cached_tokens": cached_input if isinstance(cached_input, int) else None,
            "cache_write_tokens": (
                cache_write_input if isinstance(cache_write_input, int) else None
            ),
            "cache_miss_tokens": cache_miss,
        }

    def _headers(self) -> dict[str, str]:
        """Return Anthropic authentication, version, and JSON headers."""

        return {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }

    def _finish_reason(self, value: str | None) -> str:
        """Map Anthropic stop reasons to stable platform reasons."""

        return {
            "end_turn": "stop",
            "max_tokens": "length",
            "tool_use": "tool_calls",
            "refusal": "content_filter",
            "model_context_window_exceeded": "length",
        }.get(value or "end_turn", value or "stop")

    def _validate_request(self, request: ModelRequest) -> None:
        """Reject wrong routing and portable capabilities not implemented by this codec."""

        if request.provider is not self.name:
            raise ModelProviderError(
                "invalid_request", "Request was routed to the wrong provider."
            )
        if request.json_object_output:
            raise ModelProviderError(
                "unsupported_capability",
                "Anthropic JSON object output is not implemented by this codec.",
            )
        if request.reasoning and request.reasoning.enabled:
            provider_effort = self._provider_reasoning_effort(request)
            if provider_effort not in {"low", "medium", "high", "xhigh", "max"}:
                raise ModelProviderError(
                    "unsupported_capability",
                    f"Anthropic does not support native effort '{provider_effort}'.",
                )
            if request.temperature is not None and request.temperature != 1:
                raise ModelProviderError(
                    "invalid_request",
                    "Anthropic adaptive thinking only supports temperature 1 when supplied.",
                )

    @staticmethod
    def _provider_reasoning_effort(request: ModelRequest) -> str:
        """Return the registered Anthropic effort or a direct low-level effort value."""

        if request.reasoning is None or not request.reasoning.enabled:
            raise ModelProviderError(
                "invalid_request",
                "Anthropic effort requires enabled reasoning.",
            )
        provider_value = (
            request.resolved_reasoning.provider_value
            if request.resolved_reasoning is not None
            else request.reasoning.effort.value
            if request.reasoning.effort is not None
            else "medium"
        )
        if not isinstance(provider_value, str):
            raise ModelProviderError(
                "unsupported_capability",
                "Anthropic reasoning effort mapping must resolve to a string.",
            )
        return provider_value

    def _elapsed_ms(self, started: float) -> int:
        """Return total call latency using a monotonic clock."""

        return max(0, int((time.perf_counter() - started) * 1000))
