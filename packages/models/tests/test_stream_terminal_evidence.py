"""Offline Chat SSE evidence tests; never contact a model or authorize partial tools."""

import json

import httpx
import pytest

from nexuspilot_models.contracts import ModelRequest, ProviderName
from nexuspilot_models.errors import ModelProviderError
from nexuspilot_models.providers.strata import StrataChatProvider
from nexuspilot_models.transport import HttpTransport
from nexuspilot_models.stream_progress import ModelStreamProgress


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
