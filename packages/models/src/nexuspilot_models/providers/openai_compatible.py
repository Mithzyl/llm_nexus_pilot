"""Configurable OpenAI Chat Completions compatible provider implementation."""

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

    async def stream(self, request: ModelRequest) -> AsyncIterator[StreamEvent]:
        """Normalize data-only Chat Completions SSE chunks into stable platform events."""

        self._validate_request(request)
        started = time.perf_counter()
        sequence = 1
        text_parts: list[str] = []
        reasoning_parts: list[str] = []
        tool_parts: dict[int, dict[str, str]] = {}
        finish_reason = "stop"
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
                    continue
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
                reasoning_content = delta.get("reasoning_content")
                if isinstance(reasoning_content, str) and reasoning_content:
                    reasoning_parts.append(reasoning_content)
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
            structured_output=parse_structured_output(text)
            if request.output_schema
            else None,
            finish_reason=self._finish_reason(finish_reason),
            input_tokens=usage.get("prompt_tokens"),
            output_tokens=usage.get("completion_tokens"),
            cached_tokens=self._cached_tokens(usage),
            latency_ms=self._elapsed_ms(started),
            provider_request_id=request_id,
            raw_response={
                "streamed": True,
                "text": text,
                "reasoning_content": "".join(reasoning_parts) or None,
                "tool_calls": list(tool_parts.values()),
                "finish_reason": finish_reason,
                "usage": usage,
            },
            transport_attempts=attempts,
        )
        yield StreamEvent(
            type=StreamEventType.COMPLETED,
            sequence=sequence,
            data={"response": response.model_dump(mode="json")},
        )

    def _build_payload(self, request: ModelRequest, *, stream: bool) -> dict[str, Any]:
        """Translate the public contract to OpenAI Chat Completions compatible JSON."""

        messages: list[dict[str, Any]] = []
        if request.system_instruction:
            messages.append({"role": "system", "content": request.system_instruction})
        for message in request.messages:
            item: dict[str, Any] = {
                "role": message.role.value,
                "content": message.content,
            }
            if message.role is MessageRole.TOOL:
                item["tool_call_id"] = message.tool_call_id
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
        if request.output_schema:
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
        """Normalize one compatible non-streaming completion response."""

        choices = payload.get("choices") or []
        if not choices:
            raise ModelProviderError(
                "response_parse_error",
                "Compatible provider response contains no choices.",
                transport_attempts=attempts,
            )
        choice = choices[0]
        message = choice.get("message") or {}
        text = message.get("content")
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
        return ModelResponse(
            text=text,
            tool_calls=tool_calls,
            structured_output=parse_structured_output(text)
            if request.output_schema
            else None,
            finish_reason=self._finish_reason(choice.get("finish_reason")),
            input_tokens=usage.get("prompt_tokens"),
            output_tokens=usage.get("completion_tokens"),
            cached_tokens=self._cached_tokens(usage),
            latency_ms=latency_ms,
            provider_request_id=payload.get("id"),
            raw_response=payload,
            transport_attempts=attempts,
        )

    def _headers(self) -> dict[str, str]:
        """Return authentication and content headers for a compatible endpoint."""

        headers = {"Content-Type": "application/json", **self.extra_headers}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _validate_request(self, request: ModelRequest) -> None:
        """Reject a request routed to an adapter with a different provider identity."""

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
        if request.reasoning and not self.supports_reasoning_configuration:
            raise ModelProviderError(
                "unsupported_capability",
                f"Provider '{self.name.value}' does not declare reasoning configuration support.",
            )

    def _normalize_usage(self, usage: dict[str, Any]) -> dict[str, Any]:
        """Map compatible usage fields to public stream accounting names."""

        return {
            "input_tokens": usage.get("prompt_tokens"),
            "output_tokens": usage.get("completion_tokens"),
            "cached_tokens": self._cached_tokens(usage),
        }

    def _cached_tokens(self, usage: dict[str, Any]) -> int | None:
        """Read cache usage from OpenAI-style or DeepSeek-style usage objects."""

        details = usage.get("prompt_tokens_details") or {}
        return details.get("cached_tokens") or usage.get("prompt_cache_hit_tokens")

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
