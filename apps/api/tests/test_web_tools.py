"""Offline HTTP contract checks; never send a request to a paid search provider."""

import asyncio

import httpx
import pytest

from nexuspilot_api.services.agent_file_tool_service import ToolOperationError
from nexuspilot_api.services.web_tools import DeepSeekWebAdapter, FetchArguments, SearchArguments


async def test_search_contract_preserves_empty_results_and_bounds():
    """Search sends no private question/body request and retains a legitimate empty result."""

    async def respond(request):
        """Check the actual HTTP body and return an empty successful search."""
        import json

        assert json.loads(request.content) == {
            "query": "example",
            "count": 5,
            "need_content": False,
        }
        return httpx.Response(200, json={"results": []})

    adapter = DeepSeekWebAdapter("fixture-secret", transport=httpx.MockTransport(respond))
    assert (await adapter.search(SearchArguments(query="example")))["results"] == []


async def test_search_over_real_loopback_http():
    """Exercise actual HTTP framing against a local server, never a provider endpoint."""
    import json

    received = []

    async def serve(reader, writer):
        """Read one bounded fixture request and send an actual JSON HTTP response."""
        try:
            headers = await reader.readuntil(b"\r\n\r\n")
            length = int(
                next(
                    line.split(b":", 1)[1]
                    for line in headers.split(b"\r\n")
                    if line.lower().startswith(b"content-length:")
                )
            )
            received.append(json.loads(await reader.readexactly(length)))
            body = b'{"results":[]}'
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "
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

    class LocalTransport(httpx.AsyncBaseTransport):
        """Redirect only test traffic to a loopback fixture, not runtime configuration."""

        def __init__(self):
            """Own an actual HTTP transport for this test only."""
            self.inner = httpx.AsyncHTTPTransport()

        async def handle_async_request(self, request):
            """Preserve bytes while asserting the adapter's fixed official target."""
            assert request.url.host == "api.deepseek.com"
            request.url = httpx.URL(f"http://127.0.0.1:{port}/search")
            return await self.inner.handle_async_request(request)

        async def aclose(self):
            """Close local sockets after the request."""
            await self.inner.aclose()

    try:
        result = await DeepSeekWebAdapter("fixture-secret", transport=LocalTransport()).search(
            SearchArguments(query="example")
        )
        assert result["results"] == []
        assert received == [{"query": "example", "count": 5, "need_content": False}]
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.parametrize("status", [401, 402, 403, 404, 429, 500, 302])
async def test_provider_errors_never_become_empty_success(status):
    """Failed upstream operations return a safe error rather than successful empty data."""
    adapter = DeepSeekWebAdapter(
        "fixture-secret",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(status, json={"error": {"message": "private upstream"}})
        ),
    )
    with pytest.raises(ToolOperationError):
        await adapter.search(SearchArguments(query="example"))


async def test_cancellation_propagates_to_pending_http():
    """Cancelling the caller interrupts a waiting upstream transport without retries."""
    entered = asyncio.Event()
    cancelled = asyncio.Event()

    async def wait(request):
        """Simulate a waiting supplier connection and record cancellation."""
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    adapter = DeepSeekWebAdapter("fixture-secret", transport=httpx.MockTransport(wait))
    task = asyncio.create_task(adapter.search(SearchArguments(query="example")))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cancelled.is_set()


def test_deepseek_tavily_comparison_uses_synthetic_results_not_supplier_scores():
    """Compare fixture URLs without claiming a real supplier evaluation or score equivalence."""
    from nexuspilot_api.services.web_search_comparison import compare_search_samples

    result = compare_search_samples(
        {
            "results": [
                {
                    "title": "Example",
                    "url": "https://example.com/",
                    "snippet": "Short",
                    "summary": "Long",
                }
            ]
        },
        {
            "results": [
                {"title": "Example", "url": "https://example.com/", "content": "Excerpt"},
                {"title": "Other", "url": "https://other.example/", "content": "Other excerpt"},
            ]
        },
    )
    assert result["exact_url_overlap"] == ["https://example.com/"]
    assert result["tavily_only"] == ["https://other.example/"]
    assert result["quality_verdict"] is None


@pytest.mark.parametrize("payload", [{"error": "bad"}, {"results": [{"title": "missing URL"}]}])
async def test_invalid_search_payload_is_not_success(payload):
    """Validate upstream fields before persisting any successful result."""
    adapter = DeepSeekWebAdapter(
        "fixture-secret",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload)),
    )
    with pytest.raises(ToolOperationError):
        await adapter.search(SearchArguments(query="example"))


async def test_tavily_adapter_translates_search_and_rejects_failed_extract(monkeypatch):
    """Use only the selected Tavily endpoint and reject HTTP200 extraction failures."""
    import json

    from nexuspilot_api.services.web_tools import TavilyWebAdapter

    requests = []

    async def resolve(*args, **kwargs):
        """Avoid network DNS in this protocol test."""
        return [(2, 1, 6, "", ("93.184.216.34", 443))]

    async def respond(request):
        """Assert credential placement and explicit basic options without provider fallback."""
        assert request.url.host == "api.tavily.com"
        assert request.headers["Authorization"] == "Bearer fixture-key"
        payload = json.loads(request.content)
        assert "fixture-key" not in request.content.decode()
        requests.append(request.url.path)
        if request.url.path == "/search":
            assert payload["search_depth"] == "basic" and payload["include_answer"] is False
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "title": "Example",
                            "url": "https://example.com/",
                            "content": "Public excerpt",
                        }
                    ],
                    "usage": {"credits": 1},
                },
            )
        return httpx.Response(
            200, json={"results": [], "failed_results": [{"url": payload["urls"][0]}]}
        )

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", resolve)
    adapter = TavilyWebAdapter("fixture-key", transport=httpx.MockTransport(respond))
    result = await adapter.search(SearchArguments(query="Example"))
    assert result["provider"] == "tavily" and result["results"][0]["summary"] == "Public excerpt"
    with pytest.raises(ToolOperationError, match="web_extraction_failed"):
        await adapter.fetch(FetchArguments(url="https://example.com/"))
    assert requests == ["/search", "/extract"]


async def test_tavily_successful_extract_and_fetch_offset_do_not_reach_provider(monkeypatch):
    """Keep offset selection local and accept a successful Tavily single-URL extraction."""
    import json

    from nexuspilot_api.services.web_tools import TavilyWebAdapter

    async def resolve(*args, **kwargs):
        """Provide a public fixture address."""
        return [(2, 1, 6, "", ("93.184.216.34", 443))]

    async def respond(request):
        """Reject leaked platform offset fields in the provider body."""
        payload = json.loads(request.content)
        assert payload == {
            "urls": ["https://example.com/"],
            "extract_depth": "basic",
            "format": "markdown",
            "include_usage": True,
        }
        return httpx.Response(
            200,
            json={
                "results": [{"url": "https://example.com/", "raw_content": "Excerpt"}],
                "failed_results": [],
            },
        )

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", resolve)
    result = await TavilyWebAdapter("fixture-key", transport=httpx.MockTransport(respond)).fetch(
        FetchArguments(url="https://example.com/", offset=100)
    )
    assert result["content"] == "Excerpt"


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/x",
        "http://169.254.169.254/",
        "http://user:pass@example.com",
        "file:///tmp/a",
    ],
)
def test_public_url_rejects_private_or_credential_targets(url):
    """Reject unsupported/private literal targets before a provider sees the URL."""
    with pytest.raises(ValueError):
        FetchArguments(url=url)


@pytest.mark.parametrize("mode", ["response", "agent"])
@pytest.mark.parametrize("operation", ["web_fetch", "web_search", "search_then_fetch"])
@pytest.mark.parametrize("fallback", [False, True])
async def test_web_excerpts_use_existing_loop_and_owner_checks(
    resource_client, test_database_session_factory, monkeypatch, mode, operation, fallback
):
    """Keep bounded excerpts in tool messages, without separate objects or web hashes."""
    pipeline = operation == "search_then_fetch"
    final_text = "Public page answer " + ("[S2]" if pipeline else "[S1]")

    from nexuspilot_models.contracts import ModelResponse, ToolCall
    from pydantic import SecretStr
    from sqlalchemy import select
    from test_agent_workflows import registered_agent_test_model, scripted_registry
    from test_context_builds import create_model_capability
    from test_general_agent import general_payload, unit_review_text
    from test_response_file_tools import QuickFileProvider

    from nexuspilot_api.core.config import get_settings
    from nexuspilot_api.core.dependencies import get_object_storage, get_provider_registry
    from nexuspilot_api.core.errors import ResourceNotFoundError
    from nexuspilot_api.main import app
    from nexuspilot_api.models import LlmModelAttempt, LlmModelToolCall
    from nexuspilot_api.services import web_tools

    client = resource_client
    from nexuspilot_api.features.agent_runtime.services.general_agent_orchestrator import (
        GeneralAgentOrchestrator,
    )

    def expose_failure(self, error):
        """Expose unexpected test failures without weakening production error redaction."""
        raise error.__cause__ or error

    monkeypatch.setattr(GeneralAgentOrchestrator, "_public_error_from_exception", expose_failure)
    client._transport.raise_app_exceptions = True
    owner = (await client.get("/api/v1/auth/me")).json()["user_id"]
    monkeypatch.setattr(get_settings(), "web_tool_allowed_user_ids", owner)
    monkeypatch.setattr(get_settings(), "web_search_api_key", SecretStr("fixture-web-key"))
    monkeypatch.setattr(get_settings(), "tavily_api_key", SecretStr("fixture-backup-key"))
    await create_model_capability(client, context_window=131072)
    async with test_database_session_factory() as db:
        db.add(registered_agent_test_model())
        await db.commit()
    session = (await client.post("/api/v1/sessions", json={"user_id": owner})).json()
    turn = (
        await client.post(
            f"/api/v1/sessions/{session['session_id']}/turns",
            json={"user_id": owner, "content_text": "Read a public page"},
        )
    ).json()
    calls = []

    async def resolve(*args, **kwargs):
        """Return a fixed public address without external DNS."""
        return [(2, 1, 6, "", ("93.184.216.34", 443))]

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", resolve)

    async def respond(request):
        """Return long page content or a search excerpt, never external HTTP."""
        calls.append(request.url.path)
        if fallback and request.url.host != "api.tavily.com":
            return httpx.Response(503)
        if request.url.host == "api.tavily.com":
            if request.url.path == "/search":
                return httpx.Response(
                    200,
                    json={
                        "results": [
                            {
                                "title": "Example",
                                "url": "https://example.com/",
                                "content": "WEB_ORIGINAL search excerpt",
                            }
                        ]
                    },
                )
            return httpx.Response(
                200,
                json={
                    "results": [
                        {"url": "https://example.com/page", "raw_content": "WEB_ORIGINAL " * 2000}
                    ],
                    "failed_results": [],
                },
            )
        if request.url.path.endswith("search"):
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "title": "Example",
                            "url": "https://example.com/",
                            "snippet": "WEB_ORIGINAL",
                            "summary": "WEB_ORIGINAL search excerpt",
                        }
                    ]
                },
            )
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text=("<title>Example</title><main>" + "WEB_ORIGINAL " * 2000 + "</main>"),
        )

    original = web_tools.DeepSeekWebAdapter
    monkeypatch.setattr(
        web_tools,
        "DeepSeekWebAdapter",
        lambda token: original(token, transport=httpx.MockTransport(respond)),
    )
    local_original = web_tools.LocalWebAdapter
    monkeypatch.setattr(
        web_tools, "LocalWebAdapter", lambda: local_original(transport=httpx.MockTransport(respond))
    )
    tavily_original = web_tools.TavilyWebAdapter
    monkeypatch.setattr(
        web_tools,
        "TavilyWebAdapter",
        lambda token: tavily_original(token, transport=httpx.MockTransport(respond)),
    )

    class Provider(QuickFileProvider):
        """Use native tools then submit a cited answer and deterministic review."""

        async def generate(self, request):
            """Assert the real stored text reaches the model only after a tool result."""
            if request.metadata.get("agent_role") == "reviewer":
                assert "WEB_ORIGINAL" in str(request.messages)
                return ModelResponse(
                    text=unit_review_text(request, "supported", "Checked original"),
                    finish_reason="stop",
                    input_tokens=1,
                    output_tokens=1,
                    latency_ms=1,
                    raw_response={},
                )
            results = [m for m in request.messages if m.role.value == "tool"]
            if not results:
                assert "web_fetch" in {t.name for t in request.tools}
                call = ToolCall(
                    id="fetch",
                    name="web_search" if pipeline else operation,
                    arguments=(
                        {"url": "https://example.com/page"}
                        if operation == "web_fetch"
                        else {"query": "example"}
                    ),
                )
            elif pipeline and len(results) == 1:
                call = ToolCall(
                    id="read-search-result",
                    name="web_fetch",
                    arguments={"url": "https://example.com/page"},
                )
            else:
                assert "WEB_ORIGINAL" in results[-1].content
                return ModelResponse(
                    text=final_text,
                    finish_reason="stop",
                    input_tokens=1,
                    output_tokens=1,
                    latency_ms=1,
                    raw_response={},
                )
            return ModelResponse(
                tool_calls=[call],
                finish_reason="tool_calls",
                input_tokens=1,
                output_tokens=1,
                latency_ms=1,
                raw_response={},
            )

    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(Provider())
    if mode == "response":
        response = await client.post(
            "/api/v1/responses",
            json={
                "run_id": turn["run"]["run_id"],
                "current_user_message_id": turn["message"]["message_id"],
                "provider": "openai",
                "model": "test-model",
                "input": "Read a public page",
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["output_text"] == final_text
    else:
        response = await client.post(
            f"/api/v1/runs/{turn['run']['run_id']}/agent-workflows", json=general_payload("always")
        )
        assert response.status_code == 201, response.text
        assert response.json()["status"] == "completed", response.text
    expected = ["/page"] if operation == "web_fetch" else ["/v1/tools/search"]
    if fallback:
        expected.append("/extract" if operation == "web_fetch" else "/search")
    if pipeline:
        expected.append("/page")
        if fallback:
            expected.append("/extract")
    assert calls == expected
    async with test_database_session_factory() as db:
        records = list(
            await db.scalars(
                select(LlmModelToolCall)
                .join(LlmModelAttempt)
                .where(LlmModelAttempt.run_id == turn["run"]["run_id"])
            )
        )
        assert len(records) == (2 if pipeline else 1) and all(
            r.status == "completed" for r in records
        )
        fetched = next(
            r for r in records if r.tool_name == ("web_fetch" if pipeline else operation)
        )
        assert fetched.output_json["result"]["source_kind"] == "web"
        assert fetched.result_uri is None
        result = fetched.output_json["result"]
        assert result["provider"] == (
            "tavily" if fallback else "local" if operation != "web_search" else "deepseek"
        )
        assert len(result["route_attempts"]) == (2 if fallback else 1)
        assert "source_hash" not in result and "parsed_hash" not in result
        assert "source_hash" not in result["evidence_ref"]
        assert result["read_scope"]["truncated"] is (True if operation != "web_search" else None)
        storage = app.dependency_overrides[get_object_storage]()
        if mode == "agent":
            from nexuspilot_api.models import LlmAgentWorkflowNodeExecution

            candidate = await db.scalar(
                select(LlmAgentWorkflowNodeExecution).where(
                    LlmAgentWorkflowNodeExecution.run_id == turn["run"]["run_id"],
                    LlmAgentWorkflowNodeExecution.output_type == "general_candidate",
                )
            )
            unit = next(
                u for u in candidate.output_json["units"] if u["citation_status"] == "present"
            )
            source_response = await client.get(
                f"/api/v1/agent-workflows/{candidate.workflow_execution_id}/nodes/"
                f"{candidate.node_execution_id}/claim-sources",
                params={"claim_key": unit["unit_id"]},
            )
            assert source_response.status_code == 200, source_response.text
            sources = source_response.json()["sources"]
            assert sources[0]["source_kind"] == "web"
            assert sources[0]["blocks"][0]["url"].startswith("https://example.com/")
            assert sources[0]["blocks"][0]["title"] == (
                "" if fallback and operation != "web_search" else "Example"
            )
        from nexuspilot_api.models import LlmRun, User, new_id

        db.add(User(user_id="foreign-web-owner", display_name="Synthetic other owner"))
        await db.flush()
        foreign = LlmRun(
            run_id=new_id(), user_id="foreign-web-owner", user_request="Synthetic other Run"
        )
        db.add(foreign)
        await db.commit()
        monkeypatch.setattr(
            get_settings(), "web_tool_allowed_user_ids", owner + ",foreign-web-owner"
        )
        with pytest.raises(ResourceNotFoundError):
            await web_tools.read_web_result(db, foreign.run_id, fetched.tool_call_id)
        from nexuspilot_api.services.agent_file_tool_service import AgentFileToolService

        entered = asyncio.Event()

        async def pending_fetch(adapter, arguments):
            """Hold a provider read until the owning execution is cancelled."""
            entered.set()
            await asyncio.Event().wait()

        monkeypatch.setattr(local_original, "fetch", pending_fetch)
        tools = AgentFileToolService(db, storage)
        await tools.refresh_catalog(turn["run"]["run_id"])
        pending = asyncio.create_task(
            tools.execute(
                attempt_id=fetched.attempt_id,
                call=ToolCall(
                    id="cancel-web", name="web_fetch", arguments={"url": "https://example.com/"}
                ),
            )
        )
        await asyncio.wait_for(entered.wait(), 2)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        cancelled = await db.scalar(
            select(LlmModelToolCall).where(LlmModelToolCall.provider_tool_call_id == "cancel-web")
        )
        assert cancelled.status == "cancelled" and cancelled.result_uri is None
        monkeypatch.setattr(get_settings(), "web_tool_allowed_user_ids", "")
        with pytest.raises(ResourceNotFoundError):
            await web_tools.read_web_result(db, turn["run"]["run_id"], fetched.tool_call_id)
