"""Test App authentication and the existing Agent loop with a local HTTP business peer."""

import asyncio
import json

import pytest
from nexuspilot_models.contracts import ModelResponse, ToolCall
from test_agent_workflows import register_default_agent_test_model, scripted_registry  # noqa: F401
from test_general_agent import GeneralProvider, general_model_catalog, general_payload  # noqa: F401
from test_remote_tool_adapter import remote_server  # noqa: F401

from nexuspilot_api.core.config import get_settings
from nexuspilot_api.core.dependencies import get_provider_registry
from nexuspilot_api.main import app


@pytest.fixture
def configured_application(remote_server, monkeypatch):  # noqa: F811
    """Enable one static operation with credentials used only by the local fixture."""
    from pydantic import SecretStr

    url, calls = remote_server
    configs = {
        name: {
            "inbound_key": name + "-" + "k" * 32,
            "service_token": "business-private-token",
            "base_url": url,
            "operations": {tool: "/business"},
            "allow_local_http": True,
        }
        for name, tool in [
            ("retail", "retail_search_products"),
            ("personal_assistant", "personal_assistant_search_notes"),
        ]
    }
    monkeypatch.setattr(get_settings(), "app_tools_json", SecretStr(json.dumps(configs)))
    return calls


def app_headers(app_id="retail", user="u1"):
    """Return one trusted backend's test credential and asserted external subject."""
    return {
        "Authorization": "Bearer " + app_id + "-" + "k" * 32,
        "X-Application-ID": app_id,
        "X-External-User-ID": user,
    }


@pytest.mark.parametrize("user,stream", [("u1", False), ("u2", True)])
@pytest.mark.parametrize("application_id", ["retail", "personal_assistant"])
async def test_app_run_reuses_loop_and_remote_result(
    client,
    configured_application,
    user,
    stream,
    test_database_session_factory,
    application_id,
):
    """Consume remote JSON once and replay the same App execution and cursor suffix."""
    tool_name = (
        "retail_search_products"
        if application_id == "retail"
        else "personal_assistant_search_notes"
    )

    class Provider(GeneralProvider):
        """Supply deterministic tool choices, leaving HTTP and persistence real."""

        async def generate(self, request):
            """Require scoped catalog and consume the actual result before answering."""
            self.requests.append(request)
            previous = [m for m in request.messages if m.role.value == "tool"]
            if not previous:
                return ModelResponse(
                    tool_calls=[
                        ToolCall(
                            id="describe",
                            name="describe_tools",
                            arguments={"name": tool_name, "version": "1.0.0"},
                        )
                    ],
                    finish_reason="tool_calls",
                    latency_ms=1,
                    raw_response={},
                )
            if len(previous) == 1:
                assert "required_capabilities" not in previous[0].content
                forbidden = (
                    "personal_assistant_search_notes"
                    if application_id == "retail"
                    else "retail_search_products"
                )
                assert forbidden not in [t.name for t in request.tools]
                return ModelResponse(
                    tool_calls=[ToolCall(id="read", name=tool_name, arguments={"query": "tea"})],
                    finish_reason="tool_calls",
                    latency_ms=1,
                    raw_response={},
                )
            result = json.loads(previous[-1].content)
            assert result["status"] == "completed", result
            assert result["result"]["items"] == [{"id": "tea", "label": "Tea"}]
            assert "business-private-token" not in str(request.messages)
            return ModelResponse(
                text="商品读取完成。", finish_reason="stop", latency_ms=1, raw_response={}
            )

    provider = Provider()
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)
    headers = app_headers(application_id, user=user)
    created = await client.post(
        "/api/v1/app/runs", headers=headers, json={"user_request": "Find tea"}
    )
    assert created.status_code == 201, created.text
    run_id = created.json()["run_id"]
    payload = {**general_payload("never"), "stream": stream}
    result = await client.post(
        f"/api/v1/app/runs/{run_id}/agent-workflows", headers=headers, json=payload
    )
    assert result.status_code == 201, result.text
    if stream:
        assert "text/event-stream" in result.headers["content-type"]
        assert "event: agent.node.progress" in result.text
        assert '"result_path":"/api/v1/app/agent-workflows/' in result.text
        summary = await client.get(f"/api/v1/app/runs/{run_id}/agent-workflow", headers=headers)
        workflow_id = summary.json()["workflow_execution_id"]
        result = await client.get(
            f"/api/v1/app/agent-workflows/{workflow_id}/result", headers=headers
        )
    assert result.json()["status"] == "completed", result.text
    assert len(configured_application) == 1
    assert configured_application[0][2]["external_user_id"] == user
    expected_name = (
        "retail.search_products"
        if application_id == "retail"
        else "personal_assistant.search_notes"
    )
    assert configured_application[0][2]["tool_name"] == expected_name
    assert set(configured_application[0][2]) == {
        "tool_execution_id",
        "application_id",
        "external_user_id",
        "tool_name",
        "tool_version",
        "arguments",
    }
    workflow_id = result.json()["workflow_execution_id"]
    path = f"/api/v1/app/agent-workflows/{workflow_id}/result"
    restored = await client.get(path, headers=headers)
    assert restored.status_code == 200
    assert restored.json()["final_output"] == result.json()["final_output"]
    events_path = f"/api/v1/app/agent-workflows/{workflow_id}/events"
    events = (await client.get(events_path, headers=headers)).text
    ids = [int(line[4:]) for line in events.splitlines() if line.startswith("id: ")]
    assert ids and ids == sorted(set(ids))
    middle = ids[len(ids) // 2]
    for cursor in ({"Last-Event-ID": str(middle)}, {}):
        suffix = await client.get(
            events_path,
            headers={**headers, **cursor},
            params={} if cursor else {"after_sequence": middle},
        )
        suffix_ids = [int(line[4:]) for line in suffix.text.splitlines() if line.startswith("id: ")]
        assert suffix_ids == [sequence for sequence in ids if sequence > middle]
    count = len(provider.requests)
    replay = await client.post(
        f"/api/v1/app/runs/{run_id}/agent-workflows", headers=headers, json=payload
    )
    assert replay.status_code == 200
    assert len(provider.requests) == count
    assert len(configured_application) == 1
    tail = await client.get(events_path, headers={**headers, "Last-Event-ID": str(ids[-1])})
    assert not tail.text.strip()
    other_app = "personal_assistant" if application_id == "retail" else "retail"
    for foreign in (app_headers(application_id, user="other"), app_headers(other_app)):
        assert (await client.get(path, headers=foreign)).status_code == 404
        assert (await client.get(events_path, headers=foreign)).status_code == 404
        assert (
            await client.post(f"/api/v1/app/agent-workflows/{workflow_id}/cancel", headers=foreign)
        ).status_code == 404
        assert (
            await client.post(f"/api/v1/app/runs/{run_id}/cancel", headers=foreign)
        ).status_code == 404
    assert (await client.get(path)).status_code == 401
    from sqlalchemy import text

    async with test_database_session_factory() as db:
        row = (
            await db.execute(
                text("SELECT output_json, body_ref_json FROM llm_tool_calls WHERE tool_name=:name"),
                {"name": tool_name},
            )
        ).one()
        assert (json.loads(row[0]) if isinstance(row[0], str) else row[0]) is None
        assert row[1]


async def test_app_rejects_identity_in_payload(client, configured_application):
    """Reject model/client-supplied owner fields before creating any App Run."""
    response = await client.post(
        "/api/v1/app/runs",
        headers=app_headers(),
        json={"user_request": "read", "user_id": "someone-else"},
    )
    assert response.status_code == 422
    assert configured_application == []


@pytest.mark.parametrize(
    "name,arguments,expected",
    [
        ("personal_assistant_search_notes", {"query": "private"}, "tool_not_allowed"),
        ("retail_search_products", {"query": "tea", "external_user_id": "u2"}, "invalid_arguments"),
        ("retail_search_products", {"query": "tea", "limit": 1000}, "invalid_arguments"),
        ("retail_submit_order", {}, "tool_not_allowed"),
        ("retail_search_products", {"query": "tea"}, "tool_not_loaded"),
    ],
)
async def test_rejected_app_tool_never_sends_http(
    client,
    configured_application,
    name,
    arguments,
    expected,
):
    """Enforce app scope, read-only registration and argument validation before dispatch."""

    class Provider(GeneralProvider):
        """Attempt one invalid operation after loading the legitimate read definition."""

        async def generate(self, request):
            """Consume a persisted denial and finish without retrying the remote request."""
            previous = [m for m in request.messages if m.role.value == "tool"]
            if not previous:
                call = ToolCall(
                    id="load",
                    name="describe_tools",
                    arguments={
                        "name": "retail_search_products",
                        "version": "9.0.0" if expected == "tool_not_loaded" else "1.0.0",
                    },
                )
            elif len(previous) == 1:
                call = ToolCall(id="invalid", name=name, arguments=arguments)
            else:
                assert json.loads(previous[-1].content)["error_code"] == expected
                return ModelResponse(
                    text="操作已拒绝。", finish_reason="stop", latency_ms=1, raw_response={}
                )
            return ModelResponse(
                tool_calls=[call], finish_reason="tool_calls", latency_ms=1, raw_response={}
            )

    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(Provider())
    response = await client.post(
        "/api/v1/app/runs", headers=app_headers(), json={"user_request": "read"}
    )
    run_id = response.json()["run_id"]
    result = await client.post(
        f"/api/v1/app/runs/{run_id}/agent-workflows",
        headers=app_headers(),
        json=general_payload("never"),
    )
    assert result.json()["status"] == "completed", result.text
    assert configured_application == []


@pytest.mark.parametrize("application_id", ["retail", "personal_assistant"])
@pytest.mark.parametrize("cancel_target", ["workflow", "run"])
async def test_cancel_app_during_remote_read_stops_next_model_call(
    client,
    configured_application,
    monkeypatch,
    application_id,
    cancel_target,
):
    """Both App cancellation endpoints stop an in-flight read and further generation."""
    import asyncio

    from pydantic import SecretStr

    config = json.loads(get_settings().app_tools_json.get_secret_value())
    tool_name = (
        "retail_search_products"
        if application_id == "retail"
        else "personal_assistant_search_notes"
    )
    config[application_id]["operations"][tool_name] = "/slow"
    monkeypatch.setattr(get_settings(), "app_tools_json", SecretStr(json.dumps(config)))

    class Provider(GeneralProvider):
        """Load the read and request it once, failing if cancellation permits another turn."""

        async def generate(self, request):
            """Return two complete tool calls and expose unexpected post-cancel generation."""
            self.requests.append(request)
            assert len(self.requests) <= 2
            call = (
                ToolCall(
                    id="load",
                    name="describe_tools",
                    arguments={"name": tool_name, "version": "1.0.0"},
                )
                if len(self.requests) == 1
                else ToolCall(id="read", name=tool_name, arguments={"query": "tea"})
            )
            return ModelResponse(
                tool_calls=[call], finish_reason="tool_calls", latency_ms=1, raw_response={}
            )

    provider = Provider()
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)
    headers = app_headers(application_id)
    run = (
        await client.post("/api/v1/app/runs", headers=headers, json={"user_request": "read"})
    ).json()
    pending = asyncio.create_task(
        client.post(
            f"/api/v1/app/runs/{run['run_id']}/agent-workflows",
            headers=headers,
            json={**general_payload("never"), "stream": True},
        )
    )
    try:
        for _ in range(200):
            if configured_application:
                break
            await asyncio.sleep(0.01)
        assert len(configured_application) == 1
        summary = (
            await client.get(f"/api/v1/app/runs/{run['run_id']}/agent-workflow", headers=headers)
        ).json()
        workflow_id = summary["workflow_execution_id"]
        cancel_path = (
            f"/api/v1/app/runs/{run['run_id']}/cancel"
            if cancel_target == "run"
            else f"/api/v1/app/agent-workflows/{workflow_id}/cancel"
        )
        cancelled = await client.post(
            cancel_path, headers=headers
        )
        assert cancelled.status_code == 200
        await pending
        result = (
            await client.get(f"/api/v1/app/agent-workflows/{workflow_id}/result", headers=headers)
        ).json()
        assert result["status"] == "cancelled"
        assert result["final_output"] is None
        assert len(provider.requests) == 2
        assert len(configured_application) == 1
    finally:
        if not pending.done():
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)


@pytest.mark.parametrize("outcome", ["completed", "failed", "workflow_cancel", "run_cancel"])
async def test_app_pending_replay_and_terminal_events(client, configured_application, outcome):
    """Replay running App work once and keep cancellation consistent with saved events."""
    from nexuspilot_models.errors import ModelProviderError

    started, release = asyncio.Event(), asyncio.Event()
    invocations = []

    class Provider(GeneralProvider):
        """Hold one synthetic provider response until the client retries or cancels."""

        async def generate(self, request):
            """Return a late answer or a deterministic failure without any paid request."""
            invocations.append(request)
            started.set()
            await release.wait()
            if outcome == "failed":
                raise ModelProviderError("invalid_response", "Synthetic provider failure")
            return await super().generate(request)

    provider = Provider()
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)
    headers = app_headers("personal_assistant")
    run = (await client.post(
        "/api/v1/app/runs", headers=headers, json={"user_request": "test objective"}
    )).json()
    payload = general_payload("never", stream=True)
    start_path = f"/api/v1/app/runs/{run['run_id']}/agent-workflows"
    pending = asyncio.create_task(client.post(start_path, headers=headers, json=payload))
    try:
        await asyncio.wait_for(started.wait(), 3)
        summary = (await client.get(
            f"/api/v1/app/runs/{run['run_id']}/agent-workflow", headers=headers
        )).json()
        workflow_id = summary["workflow_execution_id"]
        replay = await client.post(start_path, headers=headers, json=payload)
        assert replay.status_code == 200
        conflict = await client.post(
            start_path, headers=headers, json={**payload, "max_parallel_agents": 2}
        )
        assert conflict.status_code == 409
        result_path = f"/api/v1/app/agent-workflows/{workflow_id}/result"
        if outcome.endswith("cancel"):
            cancel_path = (
                f"/api/v1/app/runs/{run['run_id']}/cancel"
                if outcome == "run_cancel"
                else f"/api/v1/app/agent-workflows/{workflow_id}/cancel"
            )
            assert (await client.post(cancel_path, headers=headers)).status_code == 200
            cancelled = (await client.get(result_path, headers=headers)).json()
            assert cancelled["status"] == "cancelled"
            assert (await client.post(cancel_path, headers=headers)).status_code == 200
        release.set()
        await asyncio.wait_for(pending, 5)
        result = (await client.get(result_path, headers=headers)).json()
        expected = "cancelled" if outcome.endswith("cancel") else outcome
        assert result["status"] == expected
        assert bool(result["final_output"]) == (expected == "completed")
        events = (await client.get(
            f"/api/v1/app/agent-workflows/{workflow_id}/events", headers=headers
        )).text
        data = [json.loads(line[6:]) for line in events.splitlines() if line.startswith("data: ")]
        terminal = [event for event in data if event["event_type"] == f"agent.workflow.{expected}"]
        assert len(terminal) == 1
        assert data[-1]["workflow_status"] == expected
        assert data[-1]["event_type"] == f"agent.workflow.{expected}"
        if expected == "cancelled":
            assert result["aggregate_usage"]["input_tokens"] == 10
            assert result["aggregate_usage"]["output_tokens"] == 5
        assert len(invocations) == 1
    finally:
        release.set()
        await asyncio.wait_for(pending, 5)


@pytest.mark.parametrize("path,error_code", [
    ("/invalid", "remote_invalid_response"),
    ("/status/503", "remote_service_unavailable"),
    ("/slow", "tool_timeout"),
])
async def test_app_remote_failure_is_delivered_as_error(
    client, configured_application, monkeypatch, path, error_code,
):
    """Persist callback failures as failed tool results, not successful empty business data."""
    from dataclasses import replace

    from pydantic import SecretStr

    from nexuspilot_api.services.application_tools import STATIC_APP_TOOLS

    application_id = "personal_assistant"
    name = "personal_assistant_search_notes"
    config = json.loads(get_settings().app_tools_json.get_secret_value())
    config[application_id]["operations"][name] = path
    monkeypatch.setattr(get_settings(), "app_tools_json", SecretStr(json.dumps(config)))
    monkeypatch.setitem(STATIC_APP_TOOLS, application_id, tuple(
        replace(tool, timeout_seconds=0.05) if tool.name == name else tool
        for tool in STATIC_APP_TOOLS[application_id]
    ))

    class Provider(GeneralProvider):
        """Load one read operation and examine its actual saved error envelope."""

        async def generate(self, request):
            """Finish only after receiving a non-success tool result without an items fallback."""
            self.requests.append(request)
            previous = [m for m in request.messages if m.role.value == "tool"]
            if len(previous) == 2:
                result = json.loads(previous[-1].content)
                assert result["status"] != "completed"
                assert result["error_code"] == error_code
                assert not result.get("result")
                return ModelResponse(
                    text="读取失败，无法确认业务数据。", finish_reason="stop",
                    latency_ms=1, raw_response={},
                )
            call = (
                ToolCall(id="load", name="describe_tools", arguments={
                    "name": name, "version": "1.0.0",
                }) if not previous else ToolCall(id="read", name=name, arguments={"query": "tea"})
            )
            return ModelResponse(
                tool_calls=[call], finish_reason="tool_calls", latency_ms=1, raw_response={},
            )

    provider = Provider()
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)
    headers = app_headers(application_id)
    run = (await client.post(
        "/api/v1/app/runs", headers=headers, json={"user_request": "read notes"},
    )).json()
    response = await client.post(
        f"/api/v1/app/runs/{run['run_id']}/agent-workflows",
        headers=headers, json=general_payload("never"),
    )
    assert response.status_code == 201
    assert response.json()["status"] == "completed", response.text
    assert len(provider.requests) == 3
    assert len(configured_application) == 1


async def test_app_cancel_before_start_prevents_execution(client, configured_application):
    """Cancel a pending App Run idempotently without creating a workflow or model request."""
    provider = GeneralProvider()
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)
    headers = app_headers("personal_assistant")
    run = (await client.post(
        "/api/v1/app/runs", headers=headers, json={"user_request": "not started"},
    )).json()
    for _ in range(2):
        cancelled = await client.post(
            f"/api/v1/app/runs/{run['run_id']}/cancel", headers=headers,
        )
        assert cancelled.status_code == 200
        assert cancelled.json()["status"] == "cancelled"
    response = await client.post(
        f"/api/v1/app/runs/{run['run_id']}/agent-workflows",
        headers=headers, json=general_payload("never"),
    )
    assert response.status_code == 409
    assert not provider.requests
    assert not configured_application
