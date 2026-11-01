"""Offline local-fetch and fallback rules; no paid provider or external website requests."""

import asyncio
from types import SimpleNamespace

import httpx
import pytest
from pydantic import SecretStr

from nexuspilot_api.services.agent_file_tool_service import ToolOperationError
from nexuspilot_api.services.local_web_fetch import LocalWebAdapter
from nexuspilot_api.services.web_tools import FetchArguments, SearchArguments, request_web


async def public_dns(host, port):
    """Provide one explicitly public fixture address without real DNS."""
    return ["93.184.216.34"]


async def test_local_fetch_pins_ip_keeps_tls_hostname_and_extracts_text():
    """Connect only to the checked address; keep TLS verification/Host and exclude scripts."""

    async def respond(request):
        """Assert the actual transport request rather than just the URL validation result."""
        assert request.url.host == "93.184.216.34"
        assert request.headers["host"] == "example.com"
        assert request.extensions["sni_hostname"] == "example.com"
        assert "authorization" not in request.headers and "cookie" not in request.headers
        return httpx.Response(
            200,
            headers={"Content-Type": "text/html; charset=utf-8"},
            text=(
                "<html><head><title>Test &amp; page</title><script>SECRET_SCRIPT</script></head>"
                "<body><nav>NOISE</nav><main><h1>标题</h1><p>First paragraph.</p>"
                "<ul><li>Item</li></ul><pre> a = 1\n b = 2</pre>"
                "<table><tr><td>A</td><td>B</td></tr></table>"
                "<style>NO_STYLE</style></main><footer>FOOTER</footer></body></html>"
            ),
        )

    page = await LocalWebAdapter(resolver=public_dns, transport=httpx.MockTransport(respond)).fetch(
        FetchArguments(url="https://example.com/page")
    )
    assert page["title"] == "Test & page"
    assert all(
        s in page["content"] for s in ("标题", "First paragraph.", "Item", "a = 1", "A", "B")
    )
    assert all(s not in page["content"] for s in ("SECRET_SCRIPT", "NOISE", "NO_STYLE", "FOOTER"))
    assert page["final_url"] == "https://example.com/page"


@pytest.mark.parametrize(
    "target", ["http://127.0.0.1/", "http://169.254.169.254/", "https://private.example/"]
)
async def test_redirect_private_target_never_connects(target):
    """Recheck every redirect, including DNS-to-private, before opening a second connection."""
    requests = []

    async def resolve(host, port):
        """Return private DNS for a public-looking redirected hostname."""
        return ["10.0.0.1"] if host == "private.example" else await public_dns(host, port)

    async def respond(request):
        """Return a redirect that must be blocked before following it."""
        requests.append(request)
        return httpx.Response(302, headers={"location": target})

    with pytest.raises(ToolOperationError) as caught:
        await LocalWebAdapter(resolver=resolve, transport=httpx.MockTransport(respond)).fetch(
            FetchArguments(url="https://example.com/")
        )
    assert caught.value.status == "permission_denied"
    assert len(requests) == 1


async def test_redirect_relative_location_and_cookie_isolation():
    """Follow bounded relative redirects without carrying Set-Cookie or changing source identity."""
    requests = []

    async def respond(request):
        """Verify cookies from the redirect response never reach the destination."""
        assert "cookie" not in request.headers
        requests.append(request.url.path)
        if len(requests) == 1:
            return httpx.Response(302, headers={"location": "/next", "set-cookie": "secret=value"})
        return httpx.Response(200, headers={"content-type": "text/plain"}, text="Plain result")

    result = await LocalWebAdapter(
        resolver=public_dns, transport=httpx.MockTransport(respond)
    ).fetch(FetchArguments(url="https://example.com/start"))
    assert result["content"] == "Plain result" and result["final_url"] == "https://example.com/next"
    assert requests == ["/start", "/next"]


@pytest.mark.parametrize(
    "response,code",
    [
        (httpx.Response(403), "local_http_403"),
        (
            httpx.Response(200, headers={"content-type": "image/png"}, content=b"PNG"),
            "local_unsupported_content",
        ),
        (
            httpx.Response(
                200, headers={"content-type": "text/html"}, text="<script>only JS</script>"
            ),
            "local_empty_content",
        ),
        (
            httpx.Response(
                200, headers={"content-type": "text/plain"}, content=b"x" * (2 * 1024 * 1024 + 1)
            ),
            "local_response_too_large",
        ),
    ],
)
async def test_local_failures_are_explicit(response, code):
    """Distinguish HTTP failure, unsupported format, empty extraction and oversized body."""
    adapter = LocalWebAdapter(
        resolver=public_dns, transport=httpx.MockTransport(lambda req: response)
    )
    with pytest.raises(ToolOperationError, match=code):
        await adapter.fetch(FetchArguments(url="https://example.com/"))


async def test_redirect_limit_is_bounded():
    """Stop redirect loops rather than fetching indefinitely."""
    calls = []

    async def respond(request):
        """Loop a public redirect for a bounded redirect test."""
        calls.append(request)
        return httpx.Response(302, headers={"location": "/again"})

    with pytest.raises(ToolOperationError, match="local_redirect_limit"):
        await LocalWebAdapter(resolver=public_dns, transport=httpx.MockTransport(respond)).fetch(
            FetchArguments(url="https://example.com/")
        )
    assert len(calls) == 4


@pytest.mark.parametrize(
    "operation,primary_name",
    [("web_search", "DeepSeekWebAdapter"), ("web_fetch", "LocalWebAdapter")],
)
async def test_primary_failure_falls_back_once_and_records_reason(
    monkeypatch, operation, primary_name
):
    """Use Tavily exactly once for normal primary failures and retain safe route diagnostics."""
    from nexuspilot_api.services import web_tools

    calls = []

    class Primary:
        """Represent an unavailable primary without external requests."""

        def __init__(self, *args):
            """Accept the normal credential constructor when applicable."""

        async def search(self, args):
            """Fail the primary search predictably."""
            calls.append("primary")
            raise ToolOperationError("primary_unavailable")

        fetch = search

    class Backup(Primary):
        """Return a successful substitute result."""

        async def search(self, args):
            """Count the single fallback and return the expected normalized shape."""
            calls.append("tavily")
            return {"results": [], "title": "", "content": "Backup text", "usage": {"credits": 1}}

        fetch = search

    monkeypatch.setattr(web_tools, primary_name, Primary)
    monkeypatch.setattr(web_tools, "TavilyWebAdapter", Backup)
    settings = SimpleNamespace(
        web_search_api_key=SecretStr("primary"), tavily_api_key=SecretStr("backup")
    )
    args = (
        SearchArguments(query="q")
        if operation == "web_search"
        else FetchArguments(url="https://example.com/")
    )
    result = await request_web(operation, args, settings)
    assert calls == ["primary", "tavily"] and result["provider"] == "tavily"
    assert result["route_attempts"][0]["error_code"] == "primary_unavailable"


@pytest.mark.parametrize("failure", ["cancel", "denied"])
async def test_cancel_or_denied_target_never_calls_tavily(monkeypatch, failure):
    """Cancellation and forbidden targets must not be bypassed by a remote fallback."""
    from nexuspilot_api.services import web_tools

    class Primary:
        """Raise the exact terminal control signal being tested."""

        async def fetch(self, args):
            """Never return successful text for a cancelled or denied read."""
            if failure == "cancel":
                raise asyncio.CancelledError
            raise ToolOperationError("web_private_target", status="permission_denied")

    def backup(*args):
        """Fail the test if any backup is constructed."""
        pytest.fail("Tavily must not be called")

    monkeypatch.setattr(web_tools, "LocalWebAdapter", Primary)
    monkeypatch.setattr(web_tools, "TavilyWebAdapter", backup)
    settings = SimpleNamespace(web_search_api_key=None, tavily_api_key=SecretStr("backup"))
    with pytest.raises(asyncio.CancelledError if failure == "cancel" else ToolOperationError):
        await request_web("web_fetch", FetchArguments(url="https://example.com/"), settings)


async def test_empty_search_is_success_without_fallback(monkeypatch):
    """A valid empty search does not spend credits at a second provider."""
    from nexuspilot_api.services import web_tools

    class Primary:
        """Return a legitimate empty search."""

        def __init__(self, token):
            """Accept only the selected primary credential."""

        async def search(self, args):
            """Preserve an empty result rather than converting it into failure."""
            return {"results": []}

    def backup(*args):
        """Catch unexpected extra provider calls."""
        pytest.fail("Empty search must not fall back")

    monkeypatch.setattr(web_tools, "DeepSeekWebAdapter", Primary)
    monkeypatch.setattr(web_tools, "TavilyWebAdapter", backup)
    result = await request_web(
        "web_search",
        SearchArguments(query="q"),
        SimpleNamespace(
            web_search_api_key=SecretStr("primary"), tavily_api_key=SecretStr("backup")
        ),
    )
    assert result["provider"] == "deepseek" and len(result["route_attempts"]) == 1


async def test_missing_primary_key_uses_backup_and_both_failures_remain_errors(monkeypatch):
    """Missing configuration is fallback-eligible; two failures are never an empty success."""
    from nexuspilot_api.services import web_tools

    class Backup:
        """Fail the only configured route safely."""

        def __init__(self, token):
            """Assert the backup uses only its own credential."""
            assert token == "backup"

        async def search(self, args):
            """Return a safe upstream error, not an error envelope as results."""
            raise ToolOperationError("web_http_429")

    monkeypatch.setattr(web_tools, "TavilyWebAdapter", Backup)
    with pytest.raises(ToolOperationError) as caught:
        await request_web(
            "web_search",
            SearchArguments(query="q"),
            SimpleNamespace(web_search_api_key=None, tavily_api_key=SecretStr("backup")),
        )
    attempts = caught.value.result["route_attempts"]
    assert [a["provider"] for a in attempts] == ["deepseek", "tavily"]
    assert [a["error_code"] for a in attempts] == ["web_provider_not_configured", "web_http_429"]


async def test_cancelling_inflight_local_socket_does_not_start_backup(monkeypatch):
    """Propagate actual task cancellation into the pending HTTP stream, not just a fake raise."""
    from nexuspilot_api.services import web_tools

    entered = asyncio.Event()
    closed = asyncio.Event()

    async def respond(request):
        """Hold an HTTP response until cancellation closes the await."""
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            closed.set()

    monkeypatch.setattr(
        web_tools,
        "LocalWebAdapter",
        lambda: LocalWebAdapter(resolver=public_dns, transport=httpx.MockTransport(respond)),
    )

    def backup(*args):
        """Fail if cancellation causes a new supplier request."""
        pytest.fail("Cancellation must not fall back")

    monkeypatch.setattr(web_tools, "TavilyWebAdapter", backup)
    task = asyncio.create_task(
        request_web(
            "web_fetch",
            FetchArguments(url="https://example.com/"),
            SimpleNamespace(web_search_api_key=None, tavily_api_key=SecretStr("backup")),
        )
    )
    await asyncio.wait_for(entered.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert closed.is_set()


async def test_local_fetch_over_real_loopback_http():
    """Exercise HTTP streaming against a local server using test-only transport rewriting."""
    requests = []

    async def serve(reader, writer):
        """Return public-text fixtures through an actual local socket."""
        try:
            requests.append(await reader.readuntil(b"\r\n\r\n"))
            body = b"<title>Local fixture</title><main><p>Readable body</p></main>"
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nContent-Length: "
                + str(len(body)).encode()
                + b"\r\nConnection: close\r\n\r\n"
                + body
            )
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(serve, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]

    class LoopbackTransport(httpx.AsyncBaseTransport):
        """Translate an already checked fixture IP only inside the test."""

        def __init__(self):
            """Own the real HTTP transport used for this fixture."""
            self.inner = httpx.AsyncHTTPTransport()

        async def handle_async_request(self, request):
            """Assert IP pinning before rewriting to the explicitly local fixture server."""
            assert request.url.host == "93.184.216.34"
            request.url = httpx.URL(f"http://127.0.0.1:{port}/page")
            return await self.inner.handle_async_request(request)

        async def aclose(self):
            """Release actual local sockets after the call."""
            await self.inner.aclose()

    try:
        result = await LocalWebAdapter(resolver=public_dns, transport=LoopbackTransport()).fetch(
            FetchArguments(url="http://example.com/page")
        )
        assert result["title"] == "Local fixture" and result["content"] == "Readable body"
        assert len(requests) == 1 and b"Host: example.com" in requests[0]
    finally:
        server.close()
        await server.wait_closed()


async def test_general_catalog_refresh_preserves_removed_native_tool(
    monkeypatch, test_database_session_factory,
):
    """Refresh grants against isolated persistence without restoring a withdrawn native tool."""
    from nexuspilot_api.features.agent_runtime.services.task_note_tools import (
        GeneralAgentToolService,
    )
    from nexuspilot_api.services import application_tools, python_tool_service, web_tools
    from nexuspilot_api.services.tool_catalog import ToolCatalog

    async def no_grant(*args):
        """Keep web, Python and App grants absent without making external requests."""
        return None

    monkeypatch.setattr(application_tools, "authorized_application_context", no_grant)
    monkeypatch.setattr(python_tool_service, "python_authorized", no_grant)
    monkeypatch.setattr(web_tools, "web_authorized", no_grant)
    async with test_database_session_factory() as db:
        tools = GeneralAgentToolService(
            db, None, task_id="task", sources={}, allowed_task_ids=set(),
        )
        tools.catalog = ToolCatalog(
            tuple(t for t in tools.catalog.entries if t.name != "get_file_outline")
        )
        await tools.refresh_catalog("run")
        assert "get_file_outline" not in {t.name for t in tools.catalog.entries}
