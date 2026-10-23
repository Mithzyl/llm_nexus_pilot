"""Test App authentication and the existing Agent loop with a local HTTP business peer."""

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
async def test_app_run_reuses_loop_and_remote_result(
    client, configured_application, user, stream, test_database_session_factory,
):
    """Discover, load, invoke and consume remote JSON through the same durable tool loop."""

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
                            arguments={"name": "retail_search_products", "version": "1.0.0"},
                        )
                    ],
                    finish_reason="tool_calls",
                    latency_ms=1,
                    raw_response={},
                )
            if len(previous) == 1:
                assert "required_capabilities" not in previous[0].content
                assert "personal_assistant_search_notes" not in [t.name for t in request.tools]
                return ModelResponse(
                    tool_calls=[
                        ToolCall(
                            id="read", name="retail_search_products", arguments={"query": "tea"}
                        )
                    ],
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
    headers = app_headers(user=user)
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
    assert configured_application[0][2]["tool_name"] == "retail.search_products"
    workflow_id = result.json()["workflow_execution_id"]
    path = f"/api/v1/app/agent-workflows/{workflow_id}/result"
    restored = await client.get(path, headers=headers)
    assert restored.status_code == 200
    assert restored.json()["final_output"] == result.json()["final_output"]
    for foreign in (app_headers(user="other"), app_headers("personal_assistant")):
        assert (await client.get(path, headers=foreign)).status_code == 404
    assert (await client.get(path)).status_code == 401
    from sqlalchemy import text

    async with test_database_session_factory() as db:
        row = (await db.execute(text(
            "SELECT output_json, body_ref_json FROM llm_tool_calls WHERE tool_name="
            "'retail_search_products'"))).one()
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


async def test_cancel_app_during_remote_read_stops_next_model_call(
    client,
    configured_application,
    monkeypatch,
):
    """The existing cancellation endpoint stops an in-flight App read and further generation."""
    import asyncio

    from pydantic import SecretStr

    config = json.loads(get_settings().app_tools_json.get_secret_value())
    config["retail"]["operations"]["retail_search_products"] = "/slow"
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
                    arguments={"name": "retail_search_products", "version": "1.0.0"},
                )
                if len(self.requests) == 1
                else ToolCall(id="read", name="retail_search_products", arguments={"query": "tea"})
            )
            return ModelResponse(
                tool_calls=[call], finish_reason="tool_calls", latency_ms=1, raw_response={}
            )

    provider = Provider()
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)
    headers = app_headers()
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
        cancelled = await client.post(
            f"/api/v1/app/agent-workflows/{workflow_id}/cancel", headers=headers
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
