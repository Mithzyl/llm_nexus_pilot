"""Separate App Run access even when unified login maps Apps to the same resource owner."""

import json

from pydantic import SecretStr
from test_agent_workflows import register_default_agent_test_model  # noqa: F401
from test_application_tool_loop import app_headers
from test_general_agent import general_model_catalog, general_payload  # noqa: F401

from nexuspilot_api.core.config import get_settings
from nexuspilot_api.features.identity.models.applications import Application, ApplicationUserMapping


async def test_same_owner_different_apps_cannot_read_or_cancel_runs(
    client,
    monkeypatch,
    test_database_session_factory,
):
    """Require exact App mapping as well as owner for every saved Run reference."""
    settings = get_settings()
    monkeypatch.setattr(settings, "workflow_execution_backend", "worker")
    monkeypatch.setattr(
        settings,
        "app_tools_json",
        SecretStr(
            json.dumps(
                {
                    name: {
                        "inbound_key": name + "-" + "k" * 32,
                        "service_token": "fixture-private-callback",
                        "base_url": "http://127.0.0.1:18013",
                        "allow_local_http": True,
                        "operations": {
                            (
                                "retail_search_products"
                                if name == "retail"
                                else "personal_assistant_search_notes"
                            ): "/tools"
                        },
                    }
                    for name in ["retail", "personal_assistant"]
                }
            )
        ),
    )
    retail = app_headers("retail")
    assistant = app_headers("personal_assistant")
    created = await client.post(
        "/api/v1/app/runs", headers=retail, json={"user_request": "scope fixture"}
    )
    assert created.status_code == 201, created.text
    run = created.json()
    async with test_database_session_factory() as db:
        db.add(Application(application_id="personal_assistant"))
        await db.flush()
        db.add(
            ApplicationUserMapping(
                application_id="personal_assistant",
                external_user_id="u1",
                pilot_principal_id=run["user_id"],
            )
        )
        await db.commit()
    started = await client.post(
        f"/api/v1/app/runs/{run['run_id']}/agent-workflows",
        headers=retail,
        json=general_payload("never"),
    )
    assert started.status_code == 201, started.text
    workflow_id = started.json()["workflow_execution_id"]
    for method, path in [
        ("GET", f"/runs/{run['run_id']}/agent-workflow"),
        ("GET", f"/runs/{run['run_id']}/diagnostics"),
        ("GET", f"/agent-workflows/{workflow_id}/result"),
        ("GET", f"/agent-workflows/{workflow_id}/events"),
        ("POST", f"/agent-workflows/{workflow_id}/cancel"),
        ("POST", f"/runs/{run['run_id']}/cancel"),
    ]:
        denied = await client.request(method, "/api/v1/app" + path, headers=assistant)
        assert denied.status_code == 404, (path, denied.text)
    own = await client.get(f"/api/v1/app/agent-workflows/{workflow_id}/result", headers=retail)
    assert own.status_code == 200 and own.json()["status"] == "pending"
    own_cancel = await client.post(f"/api/v1/app/runs/{run['run_id']}/cancel", headers=retail)
    assert own_cancel.status_code == 200 and own_cancel.json()["status"] == "cancelled"
