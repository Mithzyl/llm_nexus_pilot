"""Google Gemini GenerateContent codec implemented on the shared transport."""

import json
import time
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import quote

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


class GeminiGenerateContentProvider:
    """Translate the platform contract to Gemini GenerateContent requests and responses."""

    name = ProviderName.GEMINI

    def __init__(
        self, transport: HttpTransport, *, base_url: str, api_key: str
    ) -> None:
        """Configure Gemini REST access without exposing its URL or credential to callers."""

        self.transport = transport
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    async def generate(self, request: ModelRequest) -> ModelResponse:
        """Call Gemini GenerateContent and normalize candidates, functions, and usage."""

        self._validate_request(request)
        started = time.perf_counter()
        result = await self.transport.post_json(
            self._url(request.model, stream=False),
            headers=self._headers(),
            payload=self._build_payload(request),
            timeout_seconds=request.timeout_seconds,
        )
        return self._parse_response(
            result.payload,
            request,
            latency_ms=self._elapsed_ms(started),
            attempts=result.attempts,
        )

    async def stream(self, request: ModelRequest) -> AsyncIterator[StreamEvent]:
        """Normalize streamed GenerateContent response objects from Gemini SSE."""

        self._validate_request(request)
        started = time.perf_counter()
        sequence = 1
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        usage: dict[str, Any] = {}
        finish_reason = "stop"
        request_id: str | None = None
        async with self.transport.open_sse(
            self._url(request.model, stream=True),
            headers=self._headers(),
            payload=self._build_payload(request),
            timeout_seconds=request.timeout_seconds,
        ) as (messages, _headers, attempts):
            yield StreamEvent(type=StreamEventType.STARTED, sequence=sequence)
            sequence += 1
            async for message in messages:
                try:
                    chunk = json.loads(message.data)
                except json.JSONDecodeError as exc:
                    raise ModelProviderError(
                        "response_parse_error",
                        "Gemini returned invalid stream JSON.",
                        transport_attempts=attempts,
                    ) from exc
                request_id = chunk.get("responseId") or request_id
                usage = chunk.get("usageMetadata") or usage
                candidates = chunk.get("candidates") or []
                if candidates:
                    candidate = candidates[0]
                    finish_reason = candidate.get("finishReason") or finish_reason
                    for part in (candidate.get("content") or {}).get("parts") or []:
                        if isinstance(part.get("text"), str):
                            value = part["text"]
                            text_parts.append(value)
                            yield StreamEvent(
                                type=StreamEventType.TEXT_DELTA,
                                sequence=sequence,
                                data={"delta": value},
                            )
                            sequence += 1
                        elif isinstance(part.get("functionCall"), dict):
                            call = part["functionCall"]
                            normalized = ToolCall(
                                id=call.get("id") or f"tool-{len(tool_calls)}",
                                name=call.get("name", ""),
                                arguments=parse_json_arguments(
                                    call.get("args", {}), context="tool call"
                                ),
                            )
                            tool_calls.append(normalized)
                            yield StreamEvent(
                                type=StreamEventType.TOOL_CALL_DELTA,
                                sequence=sequence,
                                data={
                                    "id": normalized.id,
                                    "name": normalized.name,
                                    "arguments": normalized.arguments,
                                },
                            )
                            sequence += 1

        yield StreamEvent(
            type=StreamEventType.USAGE,
            sequence=sequence,
            data=self._normalize_usage(usage),
        )
        sequence += 1
        text = "".join(text_parts) or None
        response = ModelResponse(
            text=text,
            tool_calls=tool_calls,
            structured_output=parse_structured_output(text)
            if request.output_schema
            else None,
            finish_reason=self._finish_reason(finish_reason),
            input_tokens=usage.get("promptTokenCount"),
            output_tokens=usage.get("candidatesTokenCount"),
            cached_tokens=usage.get("cachedContentTokenCount"),
            latency_ms=self._elapsed_ms(started),
            provider_request_id=request_id,
            raw_response={
                "streamed": True,
                "text": text,
                "tool_calls": [item.model_dump(mode="json") for item in tool_calls],
                "finishReason": finish_reason,
                "usageMetadata": usage,
            },
            transport_attempts=attempts,
        )
        yield StreamEvent(
            type=StreamEventType.COMPLETED,
            sequence=sequence,
            data={"response": response.model_dump(mode="json")},
        )

    def _build_payload(self, request: ModelRequest) -> dict[str, Any]:
        """Translate portable messages, tools, system text, and JSON schema to Gemini JSON."""

        contents: list[dict[str, Any]] = []
        for message in request.messages:
            if message.role is MessageRole.TOOL:
                if not message.tool_name:
                    raise ModelProviderError(
                        "invalid_request",
                        "Gemini tool result messages require tool_name.",
                    )
                contents.append(
                    {
                        "role": "user",
                        "parts": [
                            {
                                "functionResponse": {
                                    "id": message.tool_call_id,
                                    "name": message.tool_name,
                                    "response": {"output": message.content},
                                }
                            }
                        ],
                    }
                )
            else:
                role = "model" if message.role is MessageRole.ASSISTANT else "user"
                contents.append({"role": role, "parts": [{"text": message.content}]})
        payload: dict[str, Any] = {"contents": contents}
        if request.system_instruction:
            payload["systemInstruction"] = {
                "parts": [{"text": request.system_instruction}]
            }
        generation_config: dict[str, Any] = {}
        if request.temperature is not None:
            generation_config["temperature"] = request.temperature
        if request.max_output_tokens is not None:
            generation_config["maxOutputTokens"] = request.max_output_tokens
        if request.output_schema:
            generation_config["responseMimeType"] = "application/json"
            generation_config["responseJsonSchema"] = request.output_schema
        if generation_config:
            payload["generationConfig"] = generation_config
        if request.tools:
            payload["tools"] = [
                {
                    "functionDeclarations": [
                        {
                            "name": tool.name,
                            "description": tool.description,
                            "parametersJsonSchema": tool.input_schema,
                        }
                        for tool in request.tools
                    ]
                }
            ]
        return payload

    def _parse_response(
        self,
        payload: dict[str, Any],
        request: ModelRequest,
        *,
        latency_ms: int,
        attempts: list,
    ) -> ModelResponse:
        """Normalize the first Gemini candidate and its parts."""

        candidates = payload.get("candidates") or []
        if not candidates:
            raise ModelProviderError(
                "response_parse_error",
                "Gemini response contains no candidates.",
                transport_attempts=attempts,
            )
        candidate = candidates[0]
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        for part in (candidate.get("content") or {}).get("parts") or []:
            if isinstance(part.get("text"), str):
                text_parts.append(part["text"])
            elif isinstance(part.get("functionCall"), dict):
                call = part["functionCall"]
                tool_calls.append(
                    ToolCall(
                        id=call.get("id") or f"tool-{len(tool_calls)}",
                        name=call.get("name", ""),
                        arguments=parse_json_arguments(
                            call.get("args", {}), context="tool call"
                        ),
                    )
                )
        text = "".join(text_parts) or None
        usage = payload.get("usageMetadata") or {}
        return ModelResponse(
            text=text,
            tool_calls=tool_calls,
            structured_output=parse_structured_output(text)
            if request.output_schema
            else None,
            finish_reason=self._finish_reason(candidate.get("finishReason")),
            input_tokens=usage.get("promptTokenCount"),
            output_tokens=usage.get("candidatesTokenCount"),
            cached_tokens=usage.get("cachedContentTokenCount"),
            latency_ms=latency_ms,
            provider_request_id=payload.get("responseId"),
            raw_response=payload,
            transport_attempts=attempts,
        )

    def _url(self, model: str, *, stream: bool) -> str:
        """Build a fixed-origin Gemini model endpoint using an escaped model identifier."""

        operation = "streamGenerateContent?alt=sse" if stream else "generateContent"
        return f"{self.base_url}/models/{quote(model, safe='')}:{operation}"

    def _headers(self) -> dict[str, str]:
        """Return Gemini API-key and JSON headers."""

        return {"x-goog-api-key": self.api_key, "Content-Type": "application/json"}

    def _normalize_usage(self, usage: dict[str, Any]) -> dict[str, Any]:
        """Map Gemini usage metadata to public stream accounting names."""

        return {
            "input_tokens": usage.get("promptTokenCount"),
            "output_tokens": usage.get("candidatesTokenCount"),
            "cached_tokens": usage.get("cachedContentTokenCount"),
        }

    def _finish_reason(self, value: str | None) -> str:
        """Map Gemini finish reasons to stable platform reasons."""

        return {
            "STOP": "stop",
            "MAX_TOKENS": "length",
            "SAFETY": "content_filter",
            "RECITATION": "content_filter",
        }.get(value or "STOP", (value or "stop").lower())

    def _validate_request(self, request: ModelRequest) -> None:
        """Reject wrong routing and portable capabilities not implemented by this codec."""

        if request.provider is not self.name:
            raise ModelProviderError(
                "invalid_request", "Request was routed to the wrong provider."
            )
        if request.reasoning:
            raise ModelProviderError(
                "unsupported_capability",
                "Gemini reasoning configuration is not implemented by this codec.",
            )

    def _elapsed_ms(self, started: float) -> int:
        """Return total call latency using a monotonic clock."""

        return max(0, int((time.perf_counter() - started) * 1000))
