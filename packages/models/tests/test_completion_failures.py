"""Offline regressions for terminal signals and errors; never contact providers."""

import asyncio
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
from nexuspilot_models.providers.strata import StrataChatProvider
from nexuspilot_models.stream_progress import ModelStreamProgress


@pytest.mark.parametrize("collected", [False, True])
async def test_cancelled_strata_retains_partial_text_and_unexecutable_tool_arguments(collected):
    """Preserve received evidence on cancellation without completing or exposing reasoning."""
    waiting = asyncio.Event()

    class PausedStream(httpx.AsyncByteStream):
        """Return partial text, reasoning, arguments and counters before blocking."""

        async def __aiter__(self):
            """Use the actual SSE decoder and stop only after all fragments were consumed."""
            chunks = [
                {"choices": [{"delta": {"content": "已读到的草稿"}}]},
                {"choices": [{"delta": {"reasoning_content": "PRIVATE_REASONING"}}]},
                {"choices": [{"delta": {"tool_calls": [{
                    "index": 0, "id": "call-1",
                    "function": {"name": "read_file_content", "arguments": '{"offset":'},
                }]}}]},
                {"choices": [], "usage": {"prompt_tokens": 50, "completion_tokens": 12}},
            ]
            for chunk in chunks:
                yield ("data: " + json.dumps(chunk) + "\n\n").encode()
            waiting.set()
            await asyncio.Event().wait()

    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda _: httpx.Response(200, stream=PausedStream())
    )) as client:
        provider = StrataChatProvider(
            HttpTransport(client, max_retries=0), base_url="https://offline.invalid", api_key="dummy",
        )
        request = ModelRequest(
            provider=ProviderName.STRATA, model="test",
            messages=[{"role": "user", "content": "test"}],
        )

        async def consume():
            """Exercise collected Agent output and direct quick-reply streaming identically."""
            if collected:
                return await provider.generate(request)
            return [event async for event in provider.stream(request)]

        task = asyncio.create_task(consume())
        await asyncio.wait_for(waiting.wait(), timeout=2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError) as caught:
            await task
    progress = getattr(caught.value, "stream_progress", None)
    assert progress is not None
    assert progress["schema_version"] == "model_stream_progress.v1"
    assert progress["response_complete"] is False
    assert progress["text"] == "已读到的草稿"
    assert progress["tool_fragments"][0]["arguments_delta"] == '{"offset":'
    assert progress["usage"]["input_tokens"] == 50
    assert progress["usage"]["output_tokens"] == 12
    assert progress["first_output_at"] is not None
    assert progress["last_output_at"] is not None
    assert progress["reasoning_characters"] == len("PRIVATE_REASONING")
    assert "PRIVATE_REASONING" not in json.dumps(progress)
    assert "tool_calls" not in progress


@pytest.mark.parametrize("reasoning_only", [False, True])
def test_incomplete_progress_never_invents_body_or_usage(reasoning_only):
    """Silence or private thinking is not an answer, a token count, or a completed response."""
    from nexuspilot_models.contracts import StreamEvent, StreamEventType

    progress = ModelStreamProgress()
    if reasoning_only:
        progress.record(StreamEvent(
            type=StreamEventType.REASONING_RAW_DELTA, sequence=1,
            data={"delta": "PRIVATE_REASONING"},
        ))
    result = progress.snapshot()
    assert result["text"] == ""
    assert result["usage"] == {}
    assert result["first_text_at"] is None
    assert bool(result["first_output_at"]) == reasoning_only
    assert result["response_complete"] is False
    assert "PRIVATE_REASONING" not in json.dumps(result)


@pytest.mark.parametrize(
    "provider_class,name",
    [
        (OpenAIResponsesProvider, ProviderName.OPENAI),
        (AnthropicMessagesProvider, ProviderName.ANTHROPIC),
    ],
)
async def test_inband_errors_never_echo_private_provider_fields(provider_class, name):
    """HTTP-successful SSE errors must use the same safe diagnostics as HTTP failures."""
    marker = "SYNTHETIC_PRIVATE_MARKER"
    body = (
        "data: "
        + json.dumps(
            {
                "type": "error",
                "error": {
                    "message": marker,
                    "code": "server_error",
                    "private": marker,
                },
            }
        )
        + "\n\n"
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, text=body))
    ) as client:
        provider = provider_class(
            HttpTransport(client, max_retries=0),
            base_url="https://offline.invalid",
            api_key="dummy",
        )
        with pytest.raises(ModelProviderError) as caught:
            _ = [
                event
                async for event in provider.stream(
                    ModelRequest(
                        provider=name,
                        model="test",
                        messages=[{"role": "user", "content": "hi"}],
                    )
                )
            ]
    assert marker not in caught.value.message
    assert marker not in json.dumps(caught.value.raw_error)
    assert caught.value.raw_error["provider_error"]["category"] == "server_error"


@pytest.mark.parametrize(
    "status", ["failed", "cancelled", "queued", "in_progress", None, "unknown"]
)
async def test_openai_nonterminal_or_failed_json_is_not_success(status):
    """A 200 envelope without a successful terminal status must not become stop."""
    payload = {"status": status, "output": [], "error": {"message": "PRIVATE"}}
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))
    ) as client:
        provider = OpenAIResponsesProvider(
            HttpTransport(client, max_retries=0),
            base_url="https://offline.invalid",
            api_key="dummy",
        )
        with pytest.raises(ModelProviderError) as caught:
            await provider.generate(
                ModelRequest(
                    provider=ProviderName.OPENAI,
                    model="test",
                    messages=[{"role": "user", "content": "hi"}],
                )
            )
    assert "PRIVATE" not in caught.value.message


@pytest.mark.parametrize("collected", [False, True])
@pytest.mark.parametrize("ending", ["error", "eof", "network"])
async def test_strata_failure_keeps_latest_usage_in_both_consumption_modes(
    collected, ending
):
    """Real codec paths retain known counters through error, EOF and network failures."""

    class Interrupted(httpx.AsyncByteStream):
        """Send cumulative snapshots then the selected failure without using any network."""

        async def __aiter__(self):
            """Emit repeated and corrected counters to prove they are not added or maximized."""
            for output in (10, 20, 15):
                yield (
                    "data: "
                    + json.dumps(
                        {
                            "choices": [],
                            "usage": {
                                "prompt_tokens": 100,
                                "completion_tokens": output,
                            },
                        }
                    )
                    + "\n\n"
                ).encode()
            if ending == "network":
                raise httpx.ReadError("synthetic")
            if ending == "error":
                yield b'data: {"error":{"message":"PRIVATE","code":"server_error"}}\n\n'

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, stream=Interrupted())
        )
    ) as client:
        provider = StrataChatProvider(
            HttpTransport(client, max_retries=0),
            base_url="https://offline.invalid",
            api_key="dummy",
        )
        request = ModelRequest(
            provider=ProviderName.STRATA,
            model="test",
            messages=[{"role": "user", "content": "hi"}],
        )
        with pytest.raises(ModelProviderError) as caught:
            if collected:
                await provider.generate(request)
            else:
                _ = [event async for event in provider.stream(request)]
    facts = caught.value.response
    assert facts is not None
    assert (facts.input_tokens, facts.output_tokens) == (100, 15)
    assert facts.raw_response["usage_complete"] is False
    assert "PRIVATE" not in json.dumps(facts.raw_response)


async def test_openai_failed_json_keeps_known_usage_without_success():
    """Business failure can preserve reported tokens while still rejecting the answer."""
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={
                    "status": "failed",
                    "error": {"message": "PRIVATE"},
                    "usage": {"input_tokens": 100, "output_tokens": 20},
                },
            )
        )
    ) as client:
        provider = OpenAIResponsesProvider(
            HttpTransport(client, max_retries=0),
            base_url="https://offline.invalid",
            api_key="dummy",
        )
        with pytest.raises(ModelProviderError) as caught:
            await provider.generate(
                ModelRequest(
                    provider=ProviderName.OPENAI,
                    model="test",
                    messages=[{"role": "user", "content": "hi"}],
                )
            )
    assert caught.value.response.input_tokens == 100
    assert caught.value.response.output_tokens == 20
    assert "PRIVATE" not in json.dumps(caught.value.response.raw_response)


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


def test_structured_output_failure_is_classified_without_saving_raw_message():
    """Recognize provider-side JSON failure while retaining fixed public errors and no retry."""
    from nexuspilot_models.providers.openai_compatible import OpenAICompatibleChatProvider

    with pytest.raises(ModelProviderError) as caught:
        OpenAICompatibleChatProvider._check_provider_error({"error": {
            "code": "structured_output_failed",
            "type": "structured_output_failed",
            "message": "model did not return valid JSON: SECRET user@example.com",
        }}, [])
    assert caught.value.raw_error["provider_error"]["category"] == "structured_output_failed"
    assert caught.value.raw_error["provider_error"]["category_source"] == "code"
    assert caught.value.error_type == "provider_unavailable"
    assert caught.value.retryable is False
    assert "SECRET" not in json.dumps(caught.value.raw_error)
    assert "user@example.com" not in caught.value.message


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
