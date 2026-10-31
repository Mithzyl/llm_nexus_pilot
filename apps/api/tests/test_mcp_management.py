"""Check user restrictions on privately approved MCP connections without contacting servers."""

import json

import pytest
from pydantic import SecretStr
from test_authentication import enable_browser_authentication, login, provision_user  # noqa: F401


@pytest.mark.usefixtures("enable_browser_authentication")
async def test_owner_can_disable_but_not_expand_private_mcp_approval(
    resource_client, test_database_session_factory, monkeypatch
):
    """Safe metadata excludes credentials; saved preferences restrict runtime lookup."""
    from nexuspilot_api.core.config import get_settings
    from nexuspilot_api.services.mcp_management import effective_connections

    await provision_user(
        test_database_session_factory, user_id="mcp-owner", email="mcp@example.test"
    )
    assert (await login(resource_client, email="mcp@example.test")).status_code == 200
    monkeypatch.setattr(
        get_settings(),
        "mcp_connections_json",
        SecretStr(
            json.dumps(
                {
                    "approved": {
                        "url": "https://private.example/mcp",
                        "token": "never-return-this-token",
                        "user_ids": ["mcp-owner"],
                        "tools": {
                            "lookup": {
                                "description": "Approved lookup",
                                "input_schema": {
                                    "type": "object",
                                    "properties": {},
                                    "additionalProperties": False,
                                },
                            }
                        },
                    }
                }
            )
        ),
    )
    response = await resource_client.get("/api/v1/me/mcp-connections")
    assert response.status_code == 200
    assert "never-return" not in response.text and "private.example" not in response.text
    assert response.json()["items"][0]["connection"] == "approved"
    headers = {"Origin": "http://test", "X-CSRF-Token": resource_client.cookies["nexuspilot_csrf"]}
    payload = {
        "connection": "approved",
        "config_version": "1.0.0",
        "expected_revision": 0,
        "enabled": False,
        "tools": ["lookup"],
    }
    result = await resource_client.post("/api/v1/me/mcp-connections", headers=headers, json=payload)
    assert result.status_code == 200
    async with test_database_session_factory() as db:
        assert await effective_connections(db, "mcp-owner") == {}
        assert await effective_connections(db, "unrelated-owner") == {}
    rejected = await resource_client.post(
        "/api/v1/me/mcp-connections",
        headers=headers,
        json={**payload, "expected_revision": 1, "enabled": True, "tools": ["unapproved"]},
    )
    assert rejected.status_code == 422
    stale = await resource_client.post(
        "/api/v1/me/mcp-connections", headers=headers, json={**payload, "enabled": True}
    )
    assert stale.status_code == 409
    enabled = await resource_client.post(
        "/api/v1/me/mcp-connections",
        headers=headers,
        json={**payload, "expected_revision": 1, "enabled": True},
    )
    assert enabled.status_code == 200
    import httpx

    from nexuspilot_api.services import mcp_management

    fail_discovery = False

    async def remote(request):
        """Emulate JSON-RPC HTTP discovery, including an unapproved tool that must stay hidden."""
        body = json.loads(request.content)
        if fail_discovery:
            return httpx.Response(200, json={"unexpected": "not a JSON-RPC result"})
        if body["method"] == "notifications/initialized":
            return httpx.Response(202)
        result = (
            {"protocolVersion": "2025-03-26", "capabilities": {"tools": {}}}
            if body["method"] == "initialize"
            else {
                "tools": [
                    {
                        "name": "lookup",
                        "inputSchema": {
                            "type": "object",
                            "properties": {},
                            "additionalProperties": False,
                        },
                    },
                    {"name": "unapproved", "inputSchema": {"type": "object", "properties": {}}},
                ]
            }
        )
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": result})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        mcp_management.httpx,
        "AsyncClient",
        lambda **kw: real_client(transport=httpx.MockTransport(remote), **kw),
    )
    refresh = await resource_client.post(
        "/api/v1/me/mcp-connections/discover",
        headers=headers,
        json={"connection": "approved", "config_version": "1.0.0", "expected_revision": 2},
    )
    assert refresh.status_code == 200
    assert [t["name"] for t in refresh.json()["tools"]] == ["lookup"]
    metadata = await resource_client.get("/api/v1/me/mcp-connections")
    assert metadata.json()["items"][0]["discovery_status"] == "verified"
    fail_discovery = True
    failed = await resource_client.post(
        "/api/v1/me/mcp-connections/discover",
        headers=headers,
        json={"connection": "approved", "config_version": "1.0.0", "expected_revision": 2},
    )
    assert failed.status_code == 502
    metadata = await resource_client.get("/api/v1/me/mcp-connections")
    assert metadata.json()["items"][0]["discovery_status"] == "mcp_invalid_response"
