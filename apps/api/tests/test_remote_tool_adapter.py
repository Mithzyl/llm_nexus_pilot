"""Exercise bounded remote reads against a real loopback HTTP server, never business APIs."""

import asyncio
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from pydantic import BaseModel, ConfigDict

from nexuspilot_api.services.agent_file_tool_service import ToolOperationError


class Query(BaseModel):
    """Reject identity fields outside the business query."""

    model_config = ConfigDict(extra="forbid")
    query: str


class Answer(BaseModel):
    """Require the expected response envelope rather than treating failures as empty results."""

    model_config = ConfigDict(extra="forbid")
    items: list[str]


@pytest.fixture
def remote_server():
    """Run an ephemeral HTTP peer and retain received requests for assertions."""
    calls = []

    class Handler(BaseHTTPRequestHandler):
        """Respond with deterministic data or deliberately invalid bodies."""

        def log_message(self, *args):
            """Keep authorization headers and test traffic out of console logs."""

        def do_POST(self):
            """Capture the actual wire request and return the selected bounded fixture."""
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            calls.append((self.path, self.headers.get("Authorization"), body))
            if self.path == "/slow":
                time.sleep(0.2)
            status = (
                int(self.path.removeprefix("/status/")) if self.path.startswith("/status/") else 200
            )
            self.send_response(status)
            self.end_headers()
            try:
                self.wfile.write(
                    b'{"items":[{"id":"tea","label":"Tea"}]}'
                    if self.path == "/business"
                    else b"x" * 5000
                    if self.path == "/large"
                    else b'{"unexpected":true}'
                    if self.path == "/invalid"
                    else b'{"items":["real HTTP result"]}'
                )
            except (BrokenPipeError, ConnectionResetError):
                pass  # Expected when the client tests timeout or explicit cancellation.

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


async def test_remote_adapter_preserves_trusted_identity(remote_server):
    """Send server-owned identity once and validate the returned business envelope."""
    from nexuspilot_api.services.remote_tool_adapter import RemoteToolAdapter

    url, calls = remote_server
    adapter = RemoteToolAdapter(base_url=url, token="server-only", allow_local_http=True)
    result = await adapter.execute(
        path="/search",
        execution_id="exec-1",
        application_id="retail",
        external_user_id="u1",
        tool_name="search_products",
        version="1.0.0",
        arguments=Query(query="tea"),
        output=Answer,
        timeout_seconds=2,
        max_result_bytes=1024,
    )
    assert result == {"items": ["real HTTP result"]}
    assert len(calls) == 1
    assert calls[0][1] == "Bearer server-only"
    assert calls[0][2]["external_user_id"] == "u1"
    assert calls[0][2]["arguments"] == {"query": "tea"}
    assert "server-only" not in json.dumps(result)


async def test_remote_timeout_and_cancel_do_not_retry(remote_server):
    """Cancel in-flight transport promptly and retain a distinct read timeout error."""
    from nexuspilot_api.services.remote_tool_adapter import RemoteToolAdapter

    url, calls = remote_server
    adapter = RemoteToolAdapter(base_url=url, token="server-only", allow_local_http=True)
    args = dict(
        path="/slow",
        execution_id="slow",
        application_id="retail",
        external_user_id="u1",
        tool_name="search",
        version="1.0.0",
        arguments=Query(query="tea"),
        output=Answer,
        max_result_bytes=1024,
    )
    with pytest.raises(ToolOperationError) as error:
        await adapter.execute(**args, timeout_seconds=0.05)
    assert error.value.code == "remote_timeout"
    assert error.value.status == "timed_out"
    assert len(calls) == 1
    task = asyncio.create_task(adapter.execute(**args, timeout_seconds=2))
    for _ in range(100):
        if len(calls) == 2:
            break
        await asyncio.sleep(0.01)
    assert len(calls) == 2
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(calls) == 2


@pytest.mark.parametrize(
    "path,code",
    [
        ("/large", "remote_result_too_large"),
        ("/invalid", "remote_invalid_response"),
        ("/status/403", "remote_access_denied"),
        ("/status/404", "remote_resource_not_found"),
        ("/status/422", "remote_request_rejected"),
        ("/status/503", "remote_service_unavailable"),
        ("/status/302", "remote_redirect_rejected"),
    ],
)
async def test_remote_adapter_rejects_invalid_results_without_retry(remote_server, path, code):
    """Oversized and wrong-schema responses remain errors with exactly one HTTP attempt."""
    from nexuspilot_api.services.remote_tool_adapter import RemoteToolAdapter

    url, calls = remote_server
    adapter = RemoteToolAdapter(base_url=url, token="server-only", allow_local_http=True)
    with pytest.raises(ToolOperationError) as caught:
        await adapter.execute(
            path=path,
            execution_id="e",
            application_id="retail",
            external_user_id="u",
            tool_name="search",
            version="1.0.0",
            arguments=Query(query="tea"),
            output=Answer,
            timeout_seconds=2,
            max_result_bytes=1024,
        )
    assert caught.value.code == code
    assert len(calls) == 1
