"""Offline regressions for terminal signals and errors; never contact providers."""

import json

import httpx
import pytest
from nexuspilot_models.contracts import ModelRequest, ProviderName
from nexuspilot_models.errors import ModelProviderError
from nexuspilot_models.providers.anthropic import AnthropicMessagesProvider
from nexuspilot_models.providers.deepseek import DeepSeekChatProvider
from nexuspilot_models.providers.gemini import GeminiGenerateContentProvider
from nexuspilot_models.providers.openai import OpenAIResponsesProvider
from nexuspilot_models.transport import HttpTransport


@pytest.mark.parametrize(
    "exception,error_type",
    [(httpx.ReadTimeout, "timeout"), (httpx.ReadError, "network_error")],
)
async def test_midstream_network_failure_is_classified_without_replay(
    exception, error_type
):
    """Once output has arrived, a broken stream must fail once rather than retry generation."""
    requests = []

    class BrokenStream(httpx.AsyncByteStream):
        """Deliver one content delta then simulate a transport read failure."""

        async def __aiter__(self):
            """Yield fixture bytes before the explicitly selected read exception."""
            yield b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n'
            raise exception("synthetic transport failure")

    def respond(request):
        """Count actual mock dispatches to detect accidental stream replay."""
        requests.append(request)
        return httpx.Response(200, stream=BrokenStream())

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        provider = DeepSeekChatProvider(
            HttpTransport(client, max_retries=2),
            base_url="https://offline.invalid",
            api_key="dummy",
        )
        with pytest.raises(ModelProviderError) as caught:
            _ = [
                e
                async for e in provider.stream(
                    ModelRequest(
                        provider=ProviderName.DEEPSEEK,
                        model="test",
                        messages=[{"role": "user", "content": "hi"}],
                    )
                )
            ]
        assert caught.value.error_type == error_type
        assert len(requests) == 1


@pytest.mark.parametrize(
    "provider_class,name,chunk",
    [
        (
            OpenAIResponsesProvider,
            ProviderName.OPENAI,
            {"type": "response.output_text.delta", "delta": "partial"},
        ),
        (
            AnthropicMessagesProvider,
            ProviderName.ANTHROPIC,
            {
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": "partial"},
            },
        ),
        (
            GeminiGenerateContentProvider,
            ProviderName.GEMINI,
            {"candidates": [{"content": {"parts": [{"text": "partial"}]}}]},
        ),
        (
            DeepSeekChatProvider,
            ProviderName.DEEPSEEK,
            {"choices": [{"delta": {"content": "partial"}}]},
        ),
    ],
)
async def test_stream_without_terminal_status_is_not_completed(
    provider_class, name, chunk
):
    """EOF or DONE without the protocol's terminal status cannot certify completion."""
    body = "data: " + json.dumps(chunk) + "\n\n"
    if name is ProviderName.DEEPSEEK:
        body += "data: [DONE]\n\n"
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, text=body))
    ) as client:
        provider = provider_class(
            HttpTransport(client, max_retries=0),
            base_url="https://offline.invalid",
            api_key="dummy",
        )
        request = ModelRequest(
            provider=name, model="test", messages=[{"role": "user", "content": "hi"}]
        )
        with pytest.raises(ModelProviderError):
            _ = [event async for event in provider.stream(request)]


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("kind", ["error", "refusal", "empty", "missing_finish"])
async def test_chat_failure_is_not_success(stream, kind):
    """Both chat paths reject errors, refusals, empty output and absent terminal reasons."""
    field = "delta" if stream else "message"
    content = (
        {"refusal": "do not echo sensitive error"}
        if kind == "refusal"
        else {"content": None if kind == "empty" else "ok"}
    )
    chunk = (
        {"error": {"message": "do not echo sensitive error"}}
        if kind == "error"
        else {
            "choices": [
                {
                    field: content,
                    "finish_reason": None if kind == "missing_finish" else "stop",
                }
            ]
        }
    )

    def respond(_):
        """Return in-memory JSON or SSE without any network transport."""
        return (
            httpx.Response(
                200, text="data: " + json.dumps(chunk) + "\n\ndata: [DONE]\n\n"
            )
            if stream
            else httpx.Response(200, json=chunk)
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        provider = DeepSeekChatProvider(
            HttpTransport(client, max_retries=0),
            base_url="https://offline.invalid",
            api_key="dummy",
        )
        request = ModelRequest(
            provider=ProviderName.DEEPSEEK,
            model="test",
            messages=[{"role": "user", "content": "hi"}],
        )
        with pytest.raises(ModelProviderError) as caught:
            if stream:
                _ = [event async for event in provider.stream(request)]
            else:
                await provider.generate(request)
        assert "sensitive" not in str(caught.value)


def test_chat_error_diagnostic_keeps_categories_not_upstream_secrets():
    """Keep actionable private diagnostics without echoing credentials or arbitrary text."""
    from nexuspilot_models.providers.openai_compatible import (
        OpenAICompatibleChatProvider,
    )

    payload = {
        "error": {
            "code": "context_length_exceeded",
            "type": "invalid_request_error",
            "param": "max_tokens",
            "message": "maximum context length exceeded; Authorization: Bearer secret-key; user@example.com",
            "api_key": "secret-key",
        }
    }
    with pytest.raises(ModelProviderError) as caught:
        OpenAICompatibleChatProvider._check_provider_error(payload, [])
    diagnostic = caught.value.raw_error
    assert diagnostic["provider_error"]["category"] == "context_length_exceeded"
    assert diagnostic["provider_error"]["param"] == "max_tokens"
    assert len(diagnostic["provider_error"]["message_sha256"]) == 64
    assert "secret-key" not in json.dumps(diagnostic)
    assert "user@example.com" not in json.dumps(diagnostic)
    assert "context_length" not in str(caught.value)


@pytest.mark.parametrize(
    "error",
    [
        "api-key SECRET",
        {"code": "SECRET", "param": "SECRET"},
        {"code": ["SECRET"], "message": {"token": "SECRET"}},
    ],
)
def test_unknown_error_fields_are_not_preserved(error):
    """Unknown and malformed diagnostic fields cannot smuggle free text into audit records."""
    from nexuspilot_models.providers.openai_compatible import (
        OpenAICompatibleChatProvider,
    )

    with pytest.raises(ModelProviderError) as caught:
        OpenAICompatibleChatProvider._check_provider_error({"error": error}, [])
    diagnostic = caught.value.raw_error
    assert diagnostic["provider_error"]["category"] == "unknown"
    assert diagnostic["provider_error"]["category_source"] == "unclassified"
    assert "SECRET" not in json.dumps(diagnostic)


@pytest.mark.parametrize("stream", [False, True])
async def test_http_error_diagnostics_are_safe_for_both_transport_paths(stream):
    """HTTP failures retain allowlisted diagnostics without public or private secret echoes."""
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                400,
                json={
                    "error": {
                        "code": "context_length_exceeded",
                        "param": "messages",
                        "message": "Bearer SECRET user@example.com",
                        "request": "SECRET",
                    }
                },
            )
        )
    ) as client:
        transport = HttpTransport(client, max_retries=0)
        with pytest.raises(ModelProviderError) as caught:
            if stream:
                async with transport.open_sse(
                    "https://offline.invalid", headers={}, payload={}, timeout_seconds=1
                ):
                    pytest.fail("HTTP failure must not yield a successful stream")
            else:
                await transport.post_json(
                    "https://offline.invalid", headers={}, payload={}, timeout_seconds=1
                )
        diagnostic = caught.value.raw_error
        assert diagnostic["provider_error"]["category"] == "context_length_exceeded"
        assert diagnostic["http_status"] == 400
        assert "SECRET" not in json.dumps(diagnostic)
        assert "SECRET" not in str(caught.value)
        assert "user@example.com" not in json.dumps(diagnostic)
        assert "SECRET" not in str(caught.value.transport_attempts)


async def test_disabled_thinking_accepts_tool_results_without_reasoning_state():
    """Non-thinking DeepSeek calls must not require reasoning that the provider never emitted."""
    from nexuspilot_models.contracts import ReasoningConfiguration

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": "42"}, "finish_reason": "stop"}]
                },
            )
        )
    ) as client:
        provider = DeepSeekChatProvider(
            HttpTransport(client, max_retries=0),
            base_url="https://offline.invalid",
            api_key="dummy",
        )
        result = await provider.generate(
            ModelRequest(
                provider=ProviderName.DEEPSEEK,
                model="test",
                reasoning=ReasoningConfiguration(enabled=False),
                messages=[
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {"id": "call1", "name": "lookup", "arguments": {}}
                        ],
                    },
                    {"role": "tool", "content": "42", "tool_call_id": "call1"},
                ],
            )
        )
        assert result.text == "42"


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("defer_validation", [False, True])
async def test_invalid_json_preserves_private_response_and_usage(
    stream, defer_validation
):
    """A structured parse failure retains evidence while exposing only a safe error message."""
    field = "delta" if stream else "message"
    payload = {
        "choices": [{field: {"content": '{"ok":broken}'}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }

    def respond(_):
        """Supply a complete but malformed structured answer without network access."""
        return (
            httpx.Response(
                200, text="data: " + json.dumps(payload) + "\n\ndata: [DONE]\n\n"
            )
            if stream
            else httpx.Response(200, json=payload)
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        provider = DeepSeekChatProvider(
            HttpTransport(client, max_retries=0),
            base_url="https://offline.invalid",
            api_key="dummy",
        )
        request = ModelRequest(
            provider=ProviderName.DEEPSEEK,
            model="test",
            messages=[{"role": "user", "content": "JSON"}],
            json_object_output=True,
        )
        request._defer_structured_validation = defer_validation
        if defer_validation:
            if stream:
                events = [event async for event in provider.stream(request)]
                result = events[-1].data["response"]
            else:
                result = (await provider.generate(request)).model_dump()
            assert result["text"] == '{"ok":broken}'
            assert result["structured_output"] is None
            assert result["output_tokens"] == 5
            return
        with pytest.raises(ModelProviderError) as caught:
            if stream:
                _ = [event async for event in provider.stream(request)]
            else:
                await provider.generate(request)
        assert caught.value.raw_error["usage"]["completion_tokens"] == 5
        assert caught.value.transport_attempts
        assert caught.value.response.input_tokens == 10
        assert caught.value.response.output_tokens == 5
        assert "broken" not in str(caught.value)


@pytest.mark.parametrize(
    "provider_class,name,payload",
    [
        (
            OpenAIResponsesProvider,
            ProviderName.OPENAI,
            {
                "id": "fixture",
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": "invalid"}],
                    }
                ],
                "usage": {"input_tokens": 10, "output_tokens": 5},
            },
        ),
        (
            AnthropicMessagesProvider,
            ProviderName.ANTHROPIC,
            {
                "id": "fixture",
                "stop_reason": "end_turn",
                "content": [{"type": "text", "text": "invalid"}],
                "usage": {"input_tokens": 10, "output_tokens": 5},
            },
        ),
        (
            GeminiGenerateContentProvider,
            ProviderName.GEMINI,
            {
                "responseId": "fixture",
                "candidates": [
                    {
                        "finishReason": "STOP",
                        "content": {"parts": [{"text": "invalid"}]},
                    }
                ],
                "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5},
            },
        ),
    ],
)
async def test_native_codecs_keep_normalized_usage_on_invalid_json(
    provider_class, name, payload
):
    """All native codecs retain completed generation facts before strict JSON parsing fails."""
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))
    ) as client:
        provider = provider_class(
            HttpTransport(client), base_url="https://offline.invalid", api_key="dummy"
        )
        request = ModelRequest(
            provider=name,
            model="fixture",
            messages=[{"role": "user", "content": "JSON"}],
            output_schema={"type": "object"},
        )
        with pytest.raises(ModelProviderError) as caught:
            provider._parse_response(payload, request, latency_ms=42, attempts=[])
        assert caught.value.error_type == "response_parse_error"
        response = caught.value.response
        assert response.input_tokens == 10
        assert response.output_tokens == 5
        assert response.latency_ms == 42
        assert response.provider_request_id == "fixture"
