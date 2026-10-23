"""Offline Chat SSE evidence tests; never contact a model or authorize partial tools."""

import asyncio
import json

import httpx
import pytest

from nexuspilot_models.contracts import ModelRequest, ProviderName
from nexuspilot_models.errors import ModelProviderError
from nexuspilot_models.providers.strata import StrataChatProvider
from nexuspilot_models.transport import HttpTransport
from nexuspilot_models.stream_progress import ModelStreamProgress
from nexuspilot_models.providers.openai import OpenAIResponsesProvider
from nexuspilot_models.providers.anthropic import AnthropicMessagesProvider
from nexuspilot_models.providers.gemini import GeminiGenerateContentProvider


@pytest.mark.parametrize("provider_class,provider_name,chunk,expected", [
    (OpenAIResponsesProvider, ProviderName.OPENAI,
     {"type": "response.completed", "response": {"status": "completed"}},
     {"protocol": "responses_sse", "terminal_event": "response.completed"}),
    (OpenAIResponsesProvider, ProviderName.OPENAI,
     {"type": "response.incomplete", "response": {"status": "incomplete"}},
     {"protocol": "responses_sse", "terminal_event": "response.incomplete"}),
    (AnthropicMessagesProvider, ProviderName.ANTHROPIC,
     {"type": "message_delta", "delta": {"stop_reason": "end_turn"}},
     {"protocol": "messages_sse", "stop_reason": "end_turn", "message_stop_received": False}),
    (AnthropicMessagesProvider, ProviderName.ANTHROPIC,
     {"type": "message_stop"},
     {"protocol": "messages_sse", "stop_reason": None, "message_stop_received": True}),
    (GeminiGenerateContentProvider, ProviderName.GEMINI,
     {"candidates": [{"finishReason": "STOP"}]},
     {"protocol": "generate_content_sse", "finish_reason": "STOP"}),
    (AnthropicMessagesProvider, ProviderName.ANTHROPIC,
     {"type": "message_delta", "delta": {"stop_reason": "PRIVATE_VALUE"}},
     {"protocol": "messages_sse", "stop_reason": "unknown", "message_stop_received": False}),
    (GeminiGenerateContentProvider, ProviderName.GEMINI,
     {"candidates": [{"finishReason": "PRIVATE_VALUE"}]},
     {"protocol": "generate_content_sse", "finish_reason": "unknown"}),
])
async def test_native_protocol_signal_survives_subsequent_read_error(
    provider_class, provider_name, chunk, expected,
):
    """Observe each existing adapter's wire signal without accepting a transport failure."""
    class BrokenStream(httpx.AsyncByteStream):
        """Send the selected protocol event and then disconnect."""

        async def __aiter__(self):
            """Exercise real SSE parsing without a paid provider request."""
            yield ("data: " + json.dumps(chunk) + "\n\n").encode()
            raise httpx.ReadError("PRIVATE_UPSTREAM_ERROR")

    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda _: httpx.Response(200, stream=BrokenStream()),
    )) as client:
        provider = provider_class(
            HttpTransport(client, max_retries=0),
            base_url="https://offline.invalid", api_key="dummy",
        )
        request = ModelRequest(
            provider=provider_name, model="fixture",
            messages=[{"role": "user", "content": "test"}],
        )
        with pytest.raises(ModelProviderError) as caught:
            _ = [event async for event in provider.stream(request)]
    assert caught.value.error_type == "network_error"
    assert caught.value.stream_progress["protocol_evidence"] == expected
    assert caught.value.stream_progress["response_complete"] is False
    assert "PRIVATE_VALUE" not in json.dumps(caught.value.stream_progress)


@pytest.mark.parametrize("finish,done", [(None, False), ("stop", False), (None, True)])
async def test_incomplete_chat_preserves_observed_terminal_signals(finish, done):
    """Record independent finish/DONE observations without declaring an incomplete turn complete."""
    chunk = {"choices": [{"delta": {"content": "draft"}, "finish_reason": finish}]}
    body = "data: " + json.dumps(chunk) + "\n\n"
    if done:
        body += "data: [DONE]\n\n"
    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda _: httpx.Response(200, text=body),
    )) as client:
        provider = StrataChatProvider(
            HttpTransport(client, max_retries=0),
            base_url="https://offline.invalid", api_key="dummy",
        )
        request = ModelRequest(
            provider=ProviderName.STRATA, model="fixture",
            messages=[{"role": "user", "content": "test"}],
        )
        with pytest.raises(ModelProviderError) as caught:
            await provider.generate(request)
    evidence = caught.value.stream_progress["protocol_evidence"]
    assert evidence == {
        "protocol": "chat_completions_sse",
        "finish_reason": finish,
        "done_marker_received": done,
    }
    assert caught.value.stream_progress["response_complete"] is False
    assert "_stream_protocol_evidence" not in request.model_dump()


@pytest.mark.parametrize("collected", [False, True])
async def test_read_failure_after_finish_preserves_signal_without_done(collected):
    """A disconnected stream with finish_reason is not approved or silently retried."""
    class BrokenStream(httpx.AsyncByteStream):
        """Emit one finish chunk before a transport-level read failure."""

        async def __aiter__(self):
            """Provide protocol facts without exposing exception text in progress."""
            yield b'data: {"choices":[{"delta":{"content":"draft"},"finish_reason":"stop"}]}\n\n'
            raise httpx.ReadError("PRIVATE_UPSTREAM_ERROR")

    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda _: httpx.Response(200, stream=BrokenStream()),
    )) as client:
        provider = StrataChatProvider(
            HttpTransport(client, max_retries=0),
            base_url="https://offline.invalid", api_key="dummy",
        )
        request = ModelRequest(
            provider=ProviderName.STRATA, model="fixture",
            messages=[{"role": "user", "content": "test"}],
        )
        with pytest.raises(ModelProviderError) as caught:
            if collected:
                await provider.generate(request)
            else:
                _ = [event async for event in provider.stream(request)]
    progress = caught.value.stream_progress
    assert caught.value.error_type == "network_error"
    assert progress["protocol_evidence"]["finish_reason"] == "stop"
    assert progress["protocol_evidence"]["done_marker_received"] is False
    assert progress["response_complete"] is False
    assert progress["text"] == "draft"
    assert "PRIVATE_UPSTREAM_ERROR" not in json.dumps(progress)


async def test_reused_request_resets_signals_and_snapshot_is_immutable():
    """A later failed call cannot inherit DONE from a successful call or mutate its snapshot."""
    responses = iter([
        'data: {"choices":[{"delta":{"content":"ok"},"finish_reason":"stop"}]}\n\n'
        'data: [DONE]\n\n',
        'data: {"choices":[{"delta":{"content":"draft"}}]}\n\n',
    ])
    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda _: httpx.Response(200, text=next(responses)),
    )) as client:
        provider = StrataChatProvider(
            HttpTransport(client, max_retries=0),
            base_url="https://offline.invalid", api_key="dummy",
        )
        request = ModelRequest(
            provider=ProviderName.STRATA, model="fixture",
            messages=[{"role": "user", "content": "test"}],
        )
        capture = ModelStreamProgress()
        request._stream_progress_capture = capture
        result = await provider.generate(request)
        assert result.text == "ok"
        original = capture.snapshot()
        assert original["protocol_evidence"]["done_marker_received"] is True
        with pytest.raises(ModelProviderError) as caught:
            await provider.generate(request)
    assert original["protocol_evidence"]["done_marker_received"] is True
    assert caught.value.stream_progress["protocol_evidence"]["done_marker_received"] is False
    assert caught.value.stream_progress["protocol_evidence"]["finish_reason"] is None


@pytest.mark.parametrize("cancel", [False, True])
async def test_chat_complete_response_survives_close_error_but_not_cancellation(cancel):
    """A verified protocol terminal permits cleanup failure, never explicit cancellation."""
    class ClosingStream(httpx.AsyncByteStream):
        """Return a full response then fail while releasing the connection."""

        async def __aiter__(self):
            """Supply actual SSE bytes with usage and both required terminal signals."""
            yield (b'data: {"choices":[{"delta":{"content":"ok"},"finish_reason":"stop"}],'
                   b'"usage":{"prompt_tokens":7,"completion_tokens":1}}\n\n'
                   b'data: [DONE]\n\n')

        async def aclose(self):
            """Distinguish a transport cleanup error from caller cancellation."""
            if cancel:
                raise asyncio.CancelledError()
            raise httpx.CloseError("PRIVATE_CLOSE_ERROR")

    calls = []

    def reply(request):
        """Count physical requests to ensure recovery does not regenerate the answer."""
        calls.append(request)
        return httpx.Response(200, stream=ClosingStream())

    async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:
        provider = StrataChatProvider(HttpTransport(client, max_retries=0),
                                      base_url="https://offline.invalid", api_key="dummy")
        request = ModelRequest(provider=ProviderName.STRATA, model="fixture",
                               messages=[{"role": "user", "content": "test"}])
        if cancel:
            with pytest.raises(asyncio.CancelledError):
                await provider.generate(request)
        else:
            result = await provider.generate(request)
            assert result.text == "ok"
            assert result.input_tokens == 7
            assert result.output_tokens == 1
            assert result.transport_attempts[0].error_type == "stream_close_error"
            assert "PRIVATE_CLOSE_ERROR" not in result.model_dump_json()
    assert len(calls) == 1


@pytest.mark.parametrize("choice,done", [
    ({"delta": {"content": "draft"}, "finish_reason": None}, True),
    ({"delta": {"content": "draft"}, "finish_reason": "length"}, True),
    ({"delta": {"tool_calls": [{"index": 0, "id": "call-1", "function": {
        "name": "read_file_content", "arguments": '{"file_id":',
    }}]}, "finish_reason": "tool_calls"}, True),
    ({"delta": {"content": "draft"}, "finish_reason": "stop"}, False),
])
async def test_close_failure_does_not_approve_incomplete_output(choice, done):
    """Terminal cleanup recovery never bypasses missing markers or invalid tool arguments."""
    class InvalidClosingStream(httpx.AsyncByteStream):
        """Supply an incomplete turn and fail cleanup without contacting a provider."""

        async def __aiter__(self):
            """Encode the selected invalid response through the real SSE parser."""
            yield ("data: " + json.dumps({"choices": [choice]}) + "\n\n").encode()
            if done:
                yield b"data: [DONE]\n\n"

        async def aclose(self):
            """Fail transport cleanup so the adapter must retain its validation requirements."""
            raise httpx.CloseError("PRIVATE_CLOSE_ERROR")

    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda _: httpx.Response(200, stream=InvalidClosingStream()),
    )) as client:
        provider = StrataChatProvider(HttpTransport(client, max_retries=0),
                                      base_url="https://offline.invalid", api_key="dummy")
        request = ModelRequest(provider=ProviderName.STRATA, model="fixture",
                               messages=[{"role": "user", "content": "test"}])
        with pytest.raises(ModelProviderError) as caught:
            await provider.generate(request)
        assert caught.value.stream_progress["response_complete"] is False
