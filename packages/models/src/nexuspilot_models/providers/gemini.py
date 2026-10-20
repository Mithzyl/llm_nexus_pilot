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
    ProviderContinuationKind,
    ProviderContinuationState,
    ProviderName,
    StreamEvent,
    StreamEventType,
    ToolCall,
)
from nexuspilot_models.errors import ModelProviderError
from nexuspilot_models.providers.common import (
    native_content_blocks,
    parse_json_arguments,
    validate_structured_response,
    preserve_stream_usage,
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

    @preserve_stream_usage
    async def stream(self, request: ModelRequest) -> AsyncIterator[StreamEvent]:
        """Normalize GenerateContent SSE and retain private finish observations on interruption."""

        self._validate_request(request)
        started = time.perf_counter()
        sequence = 1
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        usage: dict[str, Any] = {}
        finish_reason = None
        protocol_evidence = request._stream_protocol_evidence
        protocol_evidence.clear()
        protocol_evidence.update({
            "protocol": "generate_content_sse", "finish_reason": None,
        })
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
                if not isinstance(chunk, dict) or chunk.get("error"):
                    raise ModelProviderError(
                        "provider_unavailable",
                        "Gemini returned an invalid or error stream envelope.",
                        transport_attempts=attempts,
                    )
                if (chunk.get("promptFeedback") or {}).get("blockReason"):
                    raise ModelProviderError(
                        "content_filter",
                        "Gemini blocked the prompt.",
                        transport_attempts=attempts,
                    )
                request_id = chunk.get("responseId") or request_id
                usage = chunk.get("usageMetadata") or usage
                if chunk.get("usageMetadata"):
                    yield StreamEvent(
                        type=StreamEventType.USAGE,
                        sequence=sequence,
                        data=self._normalize_usage(usage),
                    )
                    sequence += 1
                candidates = chunk.get("candidates") or []
                if candidates:
                    candidate = candidates[0]
                    finish_reason = candidate.get("finishReason") or finish_reason
                    if finish_reason:
                        protocol_evidence["finish_reason"] = (
                            finish_reason if isinstance(finish_reason, str) and finish_reason in {
                                "STOP", "MAX_TOKENS", "SAFETY", "RECITATION",
                            } else "unknown"
                        )
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

        if not finish_reason:
            raise ModelProviderError(
                "response_parse_error",
                "Gemini stream ended without a finish reason.",
                transport_attempts=attempts,
            )
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
            finish_reason=self._finish_reason(finish_reason),
            input_tokens=usage.get("promptTokenCount"),
            output_tokens=usage.get("candidatesTokenCount"),
            cached_tokens=usage.get("cachedContentTokenCount"),
            cache_miss_tokens=self._cache_miss_tokens(usage),
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
        response = validate_structured_response(response, request)
        yield StreamEvent(
            type=StreamEventType.COMPLETED,
            sequence=sequence,
            data={"response": response.model_dump(mode="json")},
        )

    def _build_payload(self, request: ModelRequest) -> dict[str, Any]:
        """Translate complete tool exchanges without dropping provider thought signatures."""

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
                if message.tool_calls:
                    state = message._provider_continuation_state
                    parts = (
                        state.signed_content_blocks
                        if state and state.kind is ProviderContinuationKind.GEMINI_PARTS
                        else ([{"text": message.content}] if message.content else [])
                        + [
                            {
                                "functionCall": {
                                    "id": call.id,
                                    "name": call.name,
                                    "args": call.arguments,
                                }
                            }
                            for call in message.tool_calls
                        ]
                    )
                    contents.append({"role": role, "parts": parts})
                    continue
                contents.append(
                    {"role": role, "parts": native_content_blocks(message, self.name)}
                )
        merged = []
        for item in contents:
            if merged and item["role"] == merged[-1]["role"] == "user":
                merged[-1]["parts"].extend(item["parts"])
            else:
                merged.append(item)
        payload: dict[str, Any] = {"contents": merged}
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
        if request.reasoning:
            generation_config["thinkingConfig"] = self._thinking_config(request)
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
        """Normalize visible parts and retain the unchanged signed tool turn privately."""

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
            if isinstance(part.get("text"), str) and not part.get("thought"):
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
        response = ModelResponse(
            text=text,
            tool_calls=tool_calls,
            provider_continuation_state=(
                ProviderContinuationState(
                    kind=ProviderContinuationKind.GEMINI_PARTS,
                    signed_content_blocks=candidate["content"]["parts"],
                )
                if tool_calls
                else None
            ),
            finish_reason=self._finish_reason(candidate.get("finishReason")),
            input_tokens=usage.get("promptTokenCount"),
            output_tokens=usage.get("candidatesTokenCount"),
            cached_tokens=usage.get("cachedContentTokenCount"),
            cache_miss_tokens=self._cache_miss_tokens(usage),
            latency_ms=latency_ms,
            provider_request_id=payload.get("responseId"),
            raw_response=payload,
            transport_attempts=attempts,
        )

        return validate_structured_response(response, request)

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
            "cache_miss_tokens": self._cache_miss_tokens(usage),
        }

    def _cache_miss_tokens(self, usage: dict[str, Any]) -> int | None:
        """Derive non-cached input from Gemini's inclusive prompt-token total."""

        input_tokens = usage.get("promptTokenCount")
        cached_tokens = usage.get("cachedContentTokenCount")
        if isinstance(input_tokens, int) and isinstance(cached_tokens, int):
            return max(0, input_tokens - cached_tokens)
        return None

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
        if request.json_object_output:
            raise ModelProviderError(
                "unsupported_capability",
                "Gemini JSON object output is not implemented by this codec.",
            )
        if request.reasoning:
            self._thinking_config(request)

    @staticmethod
    def _thinking_config(request: ModelRequest) -> dict[str, str | int]:
        """Map one registered Gemini level or budget to GenerateContent JSON."""

        if request.reasoning is None:
            raise ModelProviderError(
                "invalid_request",
                "Gemini thinking configuration requires reasoning controls.",
            )
        if not request.reasoning.enabled:
            return {"thinkingBudget": 0}
        provider_value = (
            request.resolved_reasoning.provider_value
            if request.resolved_reasoning is not None
            else request.reasoning.effort.value
            if request.reasoning.effort is not None
            else "medium"
        )
        if isinstance(provider_value, bool):
            raise ModelProviderError(
                "unsupported_capability",
                "Gemini reasoning mapping cannot resolve to a boolean.",
            )
        if isinstance(provider_value, int):
            if provider_value < -1:
                raise ModelProviderError(
                    "unsupported_capability",
                    "Gemini thinking budget must be -1 or a non-negative integer.",
                )
            return {"thinkingBudget": provider_value}
        if provider_value not in {"minimal", "low", "medium", "high"}:
            raise ModelProviderError(
                "unsupported_capability",
                f"Gemini does not support thinking level '{provider_value}'.",
            )
        return {"thinkingLevel": provider_value}

    def _elapsed_ms(self, started: float) -> int:
        """Return total call latency using a monotonic clock."""

        return max(0, int((time.perf_counter() - started) * 1000))
