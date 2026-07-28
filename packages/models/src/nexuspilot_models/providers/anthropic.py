"""Anthropic Messages API codec implemented on the shared transport."""

import json
import time
from collections.abc import AsyncIterator
from typing import Any

from nexuspilot_models.contracts import (
    MessageRole,
    ModelRequest,
    ModelResponse,
    ProviderName,
    StreamEvent,
    StreamEventType,
    ToolCall,
)
from nexuspilot_models.errors import ModelProviderError
from nexuspilot_models.providers.common import (
    parse_json_arguments,
    parse_structured_output,
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

    async def stream(self, request: ModelRequest) -> AsyncIterator[StreamEvent]:
        """Normalize Anthropic named SSE content block events and cumulative usage."""

        self._validate_request(request)
        started = time.perf_counter()
        sequence = 1
        text_parts: list[str] = []
        tools: dict[int, dict[str, str]] = {}
        input_tokens: int | None = None
        output_tokens: int | None = None
        stop_reason = "stop"
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
                    input_tokens = (native_message.get("usage") or {}).get(
                        "input_tokens"
                    )
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
                    usage = event.get("usage") or {}
                    output_tokens = usage.get("output_tokens", output_tokens)
                    yield StreamEvent(
                        type=StreamEventType.USAGE,
                        sequence=sequence,
                        data={
                            "input_tokens": input_tokens,
                            "output_tokens": output_tokens,
                            "cached_tokens": None,
                        },
                    )
                    sequence += 1
                elif event_type == "error":
                    error = event.get("error") or {}
                    raise ModelProviderError(
                        "provider_unavailable",
                        str(error.get("message") or "Anthropic stream failed."),
                        retryable=error.get("type") == "overloaded_error",
                        raw_error=error,
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
            structured_output=parse_structured_output(text)
            if request.output_schema
            else None,
            finish_reason=self._finish_reason(stop_reason),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=self._elapsed_ms(started),
            provider_request_id=request_id,
            raw_response={
                "streamed": True,
                "text": text,
                "tool_calls": list(tools.values()),
                "stop_reason": stop_reason,
                "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
            },
            transport_attempts=attempts,
        )
        yield StreamEvent(
            type=StreamEventType.COMPLETED,
            sequence=sequence,
            data={"response": response.model_dump(mode="json")},
        )

    def _build_payload(self, request: ModelRequest, *, stream: bool) -> dict[str, Any]:
        """Translate portable messages, tools, and structured output to Messages JSON."""

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
                messages.append(
                    {"role": message.role.value, "content": message.content}
                )
        payload: dict[str, Any] = {
            "model": request.model,
            "messages": messages,
            "max_tokens": request.max_output_tokens or 4096,
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
        return payload

    def _parse_response(
        self,
        payload: dict[str, Any],
        request: ModelRequest,
        *,
        latency_ms: int,
        attempts: list,
    ) -> ModelResponse:
        """Normalize Anthropic text and tool-use content blocks."""

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
        cached_tokens = usage.get("cache_read_input_tokens")
        return ModelResponse(
            text=text,
            tool_calls=tool_calls,
            structured_output=parse_structured_output(text)
            if request.output_schema
            else None,
            finish_reason=self._finish_reason(payload.get("stop_reason")),
            input_tokens=usage.get("input_tokens"),
            output_tokens=usage.get("output_tokens"),
            cached_tokens=cached_tokens,
            latency_ms=latency_ms,
            provider_request_id=payload.get("id"),
            raw_response=payload,
            transport_attempts=attempts,
        )

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
        """Reject requests routed to Anthropic with another provider identity."""

        if request.provider is not self.name:
            raise ModelProviderError(
                "invalid_request", "Request was routed to the wrong provider."
            )

    def _elapsed_ms(self, started: float) -> int:
        """Return total call latency using a monotonic clock."""

        return max(0, int((time.perf_counter() - started) * 1000))
