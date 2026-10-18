"""Verify unbounded model reads still release transport resources on explicit cancellation."""

import asyncio

import httpx
import pytest

from nexuspilot_models.transport import HttpTransport


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
async def test_unbounded_read_cancellation_closes_response_without_retry(streaming):
    """Cancel blocked JSON/SSE reads, close the response and never repeat the HTTP request."""
    reading = asyncio.Event()
    closed = asyncio.Event()
    calls = []

    class WaitingBody(httpx.AsyncByteStream):
        """Represent an upstream that waits indefinitely until its reader is cancelled."""

        async def __aiter__(self):
            """Expose the blocked-read point without relying on wall-clock time."""
            reading.set()
            await asyncio.Event().wait()
            yield b""

        async def aclose(self):
            """Record release of the upstream response body."""
            closed.set()

    async def upstream(request):
        """Capture the timeout settings and provide a cancellable streaming body."""
        calls.append(request)
        assert request.extensions["timeout"] == {
            "connect": 10, "read": None, "write": 30, "pool": 30,
        }
        return httpx.Response(200, stream=WaitingBody())

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
        transport = HttpTransport(client)

        async def consume():
            """Read through the same transport methods used by model adapters."""
            options = dict(headers={}, payload={}, timeout_seconds=None)
            if streaming:
                async with transport.open_sse("https://provider.test", **options) as response:
                    async for _ in response[0]:
                        pass
            else:
                await transport.post_json("https://provider.test", **options)

        task = asyncio.create_task(consume())
        try:
            await asyncio.wait_for(reading.wait(), timeout=2)
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert closed.is_set()
        assert len(calls) == 1
