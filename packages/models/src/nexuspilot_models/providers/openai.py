"""OpenAI Responses API codec implemented on the provider-neutral transport."""

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


class OpenAIResponsesProvider:
    """Translate the platform contract to and from the OpenAI Responses API."""

    name = ProviderName.OPENAI

    def __init__(
        self, transport: HttpTransport, *, base_url: str, api_key: str
    ) -> None:
        """Configure the OpenAI endpoint and credential without owning the shared transport."""

        self.transport = transport
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    async def generate(self, request: ModelRequest) -> ModelResponse:
        """Call OpenAI Responses and normalize output items and token accounting."""

        self._validate_request(request)
        started = time.perf_counter()
        result = await self.transport.post_json(
            f"{self.base_url}/responses",
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
        """Normalize OpenAI Responses events without exposing native event names to clients."""

        self._validate_request(request)
        started = time.perf_counter()
        sequence = 1
        text_parts: list[str] = []
        tool_parts: dict[str, dict[str, str]] = {}
        final_payload: dict[str, Any] | None = None
        async with self.transport.open_sse(
            f"{self.base_url}/responses",
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
                        "OpenAI returned invalid stream JSON.",
                        transport_attempts=attempts,
                    ) from exc
                event_type = event.get("type")
                if event_type == "response.output_text.delta":
                    delta = event.get("delta", "")
                    text_parts.append(delta)
                    yield StreamEvent(
                        type=StreamEventType.TEXT_DELTA,
                        sequence=sequence,
                        data={"delta": delta},
                    )
                    sequence += 1
                elif event_type == "response.function_call_arguments.delta":
                    item_id = event.get("item_id") or str(event.get("output_index", 0))
                    current = tool_parts.setdefault(
                        item_id,
                        {"id": item_id, "name": event.get("name", ""), "arguments": ""},
                    )
                    delta = event.get("delta", "")
                    current["arguments"] += delta
                    yield StreamEvent(
                        type=StreamEventType.TOOL_CALL_DELTA,
                        sequence=sequence,
                        data={
                            "id": current["id"],
                            "name": current["name"],
                            "arguments_delta": delta,
                        },
                    )
                    sequence += 1
                elif event_type == "response.output_item.added":
                    item = event.get("item") or {}
                    if item.get("type") == "function_call":
                        item_id = item.get("id") or str(event.get("output_index", 0))
                        tool_parts[item_id] = {
                            "id": item.get("call_id") or item_id,
                            "name": item.get("name", ""),
                            "arguments": item.get("arguments", ""),
                        }
                elif event_type in {"response.completed", "response.incomplete"}:
                    final_payload = event.get("response") or {}
                    usage = final_payload.get("usage") or {}
                    yield StreamEvent(
                        type=StreamEventType.USAGE,
                        sequence=sequence,
                        data=self._normalize_usage(usage),
                    )
                    sequence += 1
                elif event_type in {"response.failed", "error"}:
                    error = (
                        event.get("error")
                        or (event.get("response") or {}).get("error")
                        or {}
                    )
                    raise ModelProviderError(
                        "provider_unavailable",
                        str(error.get("message") or "OpenAI stream failed."),
                        retryable=True,
                        raw_error=error if isinstance(error, dict) else {},
                        transport_attempts=attempts,
                    )

        if final_payload:
            response = self._parse_response(
                final_payload,
                request,
                latency_ms=self._elapsed_ms(started),
                attempts=attempts,
            )
        else:
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
                    for value in tool_parts.values()
                ],
                structured_output=parse_structured_output(text)
                if request.output_schema
                else None,
                finish_reason="stop",
                latency_ms=self._elapsed_ms(started),
                raw_response={"streamed": True},
                transport_attempts=attempts,
            )
        yield StreamEvent(
            type=StreamEventType.COMPLETED,
            sequence=sequence,
            data={"response": response.model_dump(mode="json")},
        )

    def _build_payload(self, request: ModelRequest, *, stream: bool) -> dict[str, Any]:
        """Translate portable messages, functions, and JSON Schema to Responses input."""

        input_items: list[dict[str, Any]] = []
        for message in request.messages:
            if message.role is MessageRole.TOOL:
                input_items.append(
                    {
                        "type": "function_call_output",
                        "call_id": message.tool_call_id,
                        "output": message.content,
                    }
                )
            else:
                input_items.append(
                    {"role": message.role.value, "content": message.content}
                )
        payload: dict[str, Any] = {
            "model": request.model,
            "input": input_items,
            "stream": stream,
            "store": False,
        }
        if request.system_instruction:
            payload["instructions"] = request.system_instruction
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.max_output_tokens is not None:
            payload["max_output_tokens"] = request.max_output_tokens
        if request.tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.input_schema,
                    "strict": tool.strict,
                }
                for tool in request.tools
            ]
        if request.output_schema:
            payload["text"] = {
                "format": {
                    "type": "json_schema",
                    "name": "nexuspilot_output",
                    "strict": True,
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
        """Normalize Responses output items, usage, status, and incomplete reasons."""

        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        for item in payload.get("output") or []:
            if item.get("type") == "message":
                for content in item.get("content") or []:
                    if content.get("type") == "output_text":
                        text_parts.append(content.get("text", ""))
            elif item.get("type") == "function_call":
                tool_calls.append(
                    ToolCall(
                        id=item.get("call_id") or item.get("id", ""),
                        name=item.get("name", ""),
                        arguments=parse_json_arguments(
                            item.get("arguments", "{}"), context="tool call"
                        ),
                    )
                )
        text = "".join(text_parts) or None
        usage = payload.get("usage") or {}
        input_details = usage.get("input_tokens_details") or {}
        incomplete = payload.get("incomplete_details") or {}
        finish_reason = (
            incomplete.get("reason")
            if payload.get("status") == "incomplete"
            else "tool_calls"
            if tool_calls and not text
            else "stop"
        )
        return ModelResponse(
            text=text,
            tool_calls=tool_calls,
            structured_output=parse_structured_output(text)
            if request.output_schema
            else None,
            finish_reason=finish_reason or payload.get("status", "stop"),
            input_tokens=usage.get("input_tokens"),
            output_tokens=usage.get("output_tokens"),
            cached_tokens=input_details.get("cached_tokens"),
            latency_ms=latency_ms,
            provider_request_id=payload.get("id"),
            raw_response=payload,
            transport_attempts=attempts,
        )

    def _normalize_usage(self, usage: dict[str, Any]) -> dict[str, Any]:
        """Map Responses usage to the stable stream usage event."""

        return {
            "input_tokens": usage.get("input_tokens"),
            "output_tokens": usage.get("output_tokens"),
            "cached_tokens": (usage.get("input_tokens_details") or {}).get(
                "cached_tokens"
            ),
        }

    def _headers(self) -> dict[str, str]:
        """Return OpenAI authentication and JSON headers."""

        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _validate_request(self, request: ModelRequest) -> None:
        """Reject wrong routing and portable capabilities not implemented by this codec."""

        if request.provider is not self.name:
            raise ModelProviderError(
                "invalid_request", "Request was routed to the wrong provider."
            )
        if request.reasoning:
            raise ModelProviderError(
                "unsupported_capability",
                "OpenAI reasoning configuration is not implemented by this codec.",
            )

    def _elapsed_ms(self, started: float) -> int:
        """Return total call latency using a monotonic clock."""

        return max(0, int((time.perf_counter() - started) * 1000))
