"""Test minimal personal-key management through genuine browser and resource authorization."""

import pytest
from test_authentication import login, provision_user

from nexuspilot_api.core.config import get_settings


@pytest.fixture(autouse=True)
def authentication_enabled(monkeypatch):
    """Enable user authentication explicitly without changing local runtime configuration."""
    monkeypatch.setenv("NEXUSPILOT_AUTHENTICATION_ENABLED", "true")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def test_create_list_use_and_revoke_personal_key(
    resource_client, test_database_session_factory
):
    """Return the secret once; authorize own resources without a deployment key, then revoke."""
    client = resource_client
    await provision_user(test_database_session_factory)
    assert (await login(client)).status_code == 200
    csrf = {"Origin": "http://test", "X-CSRF-Token": client.cookies["nexuspilot_csrf"]}
    created = await client.post(
        "/api/v1/me/api-keys", headers=csrf, json={"name": "script", "scopes": ["read", "execute"]}
    )
    assert created.status_code == 201, created.text
    key = created.json()["key"]
    key_id = created.json()["api_key_id"]
    listing = await client.get("/api/v1/me/api-keys")
    assert key not in listing.text
    assert listing.json()[0]["api_key_id"] == key_id
    cookies = dict(client.cookies)
    client.cookies.clear()
    client.headers.pop("X-API-Key", None)
    bearer = {"Authorization": "Bearer " + key}
    me = await client.get("/api/v1/auth/me", headers=bearer)
    assert me.status_code == 200, me.text
    user_id = me.json()["user_id"]
    run = await client.post(
        "/api/v1/runs", headers=bearer, json={"user_id": user_id, "user_request": "hello"}
    )
    assert run.status_code == 201, run.text
    assert (await client.get("/api/v1/me/api-keys", headers=bearer)).status_code == 401
    client.cookies.update(cookies)
    assert (await client.get("/api/v1/auth/me", headers=bearer)).status_code == 400
    revoked = await client.delete(f"/api/v1/me/api-keys/{key_id}", headers=csrf)
    assert revoked.status_code == 204
    client.cookies.clear()
    assert (await client.get("/api/v1/runs", headers=bearer)).status_code == 401


async def test_read_key_cannot_execute_or_choose_another_owner(
    resource_client, test_database_session_factory
):
    """Read permission is insufficient for mutations, and owner claims do not grant access."""
    client = resource_client
    await provision_user(test_database_session_factory)
    await login(client)
    csrf = {"Origin": "http://test", "X-CSRF-Token": client.cookies["nexuspilot_csrf"]}
    response = await client.post(
        "/api/v1/me/api-keys", headers=csrf, json={"name": "read only", "scopes": ["read"]}
    )
    assert response.status_code == 201, response.text
    client.cookies.clear()
    client.headers.pop("X-API-Key", None)
    headers = {"Authorization": "Bearer " + response.json()["key"]}
    assert (await client.get("/api/v1/runs", headers=headers)).status_code == 200
    assert (
        await client.post(
            "/api/v1/runs", headers=headers, json={"user_id": "other", "user_request": "hello"}
        )
    ).status_code == 403
    assert (await client.get("/api/v1/users/other", headers=headers)).status_code == 404


@pytest.mark.parametrize("invalid_state", ["expired", "revoked", "disabled"])
async def test_personal_key_invalid_state_rejects_access(
    resource_client,
    test_database_session_factory,
    invalid_state,
):
    """Expiry, revocation and disabled owners deny access even with a valid deployment key."""
    from datetime import timedelta

    from nexuspilot_api.features.identity.models.personal_api_key import UserApiKey
    from nexuspilot_api.models import User
    from nexuspilot_api.models.base import utc_now

    client = resource_client
    await provision_user(test_database_session_factory)
    await login(client)
    created = (
        await client.post(
            "/api/v1/me/api-keys",
            headers={"Origin": "http://test", "X-CSRF-Token": client.cookies["nexuspilot_csrf"]},
            json={"name": "temporary", "scopes": ["read", "execute"]},
        )
    ).json()
    async with test_database_session_factory() as db:
        row = await db.get(UserApiKey, created["api_key_id"])
        assert created["key"] not in row.verifier
        if invalid_state == "expired":
            row.expires_at = utc_now() - timedelta(seconds=1)
        elif invalid_state == "revoked":
            row.revoked_at = utc_now()
        else:
            user = await db.get(User, row.user_id)
            user.is_active = False
        await db.commit()
    client.cookies.clear()
    assert (
        await client.get("/api/v1/runs", headers={"Authorization": "Bearer " + created["key"]})
    ).status_code == 401


async def test_personal_keys_cannot_cross_user_resources(
    resource_client, test_database_session_factory
):
    """Two actual browser-issued credentials retain distinct resource and management ownership."""
    client = resource_client
    keys = []
    runs = []
    resources = []
    for user in ("key-a", "key-b"):
        client.cookies.clear()
        await provision_user(test_database_session_factory, user_id=user, email=f"{user}@test.com")
        await login(client, email=f"{user}@test.com")
        created = (
            await client.post(
                "/api/v1/me/api-keys",
                headers={
                    "Origin": "http://test",
                    "X-CSRF-Token": client.cookies["nexuspilot_csrf"],
                },
                json={"name": "client", "scopes": ["read", "execute"]},
            )
        ).json()
        keys.append(created)
        client.cookies.clear()
        response = await client.post(
            "/api/v1/runs",
            headers={"Authorization": "Bearer " + created["key"]},
            json={"user_id": user, "user_request": "private"},
        )
        assert response.status_code == 201
        runs.append(response.json()["run_id"])
        headers = {"Authorization": "Bearer " + created["key"]}
        session = await client.post(
            "/api/v1/sessions", headers=headers, json={"user_id": user, "title": "private session"}
        )
        assert session.status_code == 201
        from nexuspilot_api.models import LlmProject

        async with test_database_session_factory() as db:
            project = LlmProject(owner_user_id=user, project_name="private project")
            db.add(project)
            await db.commit()
            project_id = project.project_id
        import hashlib

        body = b"private file text"
        file = await client.post(
            "/api/v1/files",
            headers=headers,
            json={
                "filename": "private.txt",
                "size_bytes": len(body),
                "content_hash": hashlib.sha256(body).hexdigest(),
                "idempotency_key": "personal-file",
            },
        )
        assert file.status_code == 201, file.text
        file_id = file.json()["file_id"]
        uploaded = await client.put(
            f"/api/v1/files/{file_id}/content", headers=headers, content=body
        )
        assert uploaded.status_code == 200, uploaded.text
        resources.append(
            [
                f"/api/v1/sessions/{session.json()['session_id']}",
                f"/api/v1/projects/{project_id}",
                f"/api/v1/files/{file_id}/content",
            ]
        )
    for index in (0, 1):
        headers = {"Authorization": "Bearer " + keys[index]["key"]}
        assert (await client.get(f"/api/v1/runs/{runs[index]}", headers=headers)).status_code == 200
        assert (
            await client.post(f"/api/v1/runs/{runs[1 - index]}/cancel", headers=headers)
        ).status_code == 404
        assert (
            await client.post(f"/api/v1/runs/{runs[index]}/cancel", headers=headers)
        ).status_code == 200
        listed = (await client.get("/api/v1/runs", headers=headers)).json()
        assert {r["run_id"] for r in listed["items"]} == {runs[index]}
        for path in resources[index]:
            assert (await client.get(path, headers=headers)).status_code == 200
        for path in resources[1 - index]:
            assert (await client.get(path, headers=headers)).status_code == 404


async def test_personal_key_uses_existing_provider_for_response_and_agent(
    resource_client,
    test_database_session_factory,
):
    """Reuse the configured provider without exposing either credential in model messages."""
    from conftest import FakeProvider
    from sqlalchemy import select
    from test_agent_workflows import registered_agent_test_model, scripted_registry
    from test_general_agent import general_payload

    from nexuspilot_api.core.dependencies import get_provider_registry
    from nexuspilot_api.main import app
    from nexuspilot_api.models import LlmModelAttempt, LlmRun

    class Provider(FakeProvider):
        """Represent the existing server-configured adapter, with no per-user credential copy."""

        def __init__(self):
            """Retain test observations and a server-only credential marker."""
            self.requests = []
            self.server_credential = "platform-provider-secret-marker"

        async def generate(self, request):
            """Capture effective requests while using the pre-existing fake response behavior."""
            self.requests.append(request)
            if request.metadata.get("agent_role") and not any(
                message.role.value == "tool" for message in request.messages
            ):
                from nexuspilot_models.contracts import ModelResponse, ToolCall

                return ModelResponse(
                    tool_calls=[ToolCall(id="own-files", name="list_run_files", arguments={})],
                    finish_reason="tool_calls",
                    input_tokens=10,
                    output_tokens=5,
                    latency_ms=1,
                    raw_response={"text": None},
                )
            return await super().generate(request)

    async with test_database_session_factory() as db:
        db.add(registered_agent_test_model())
        await db.commit()
    provider = Provider()
    registry = scripted_registry(provider)
    app.dependency_overrides[get_provider_registry] = lambda: registry
    client = resource_client
    await provision_user(test_database_session_factory)
    await login(client)
    created = await client.post(
        "/api/v1/me/api-keys",
        json={"name": "execution", "scopes": ["read", "execute"]},
        headers={"Origin": "http://test", "X-CSRF-Token": client.cookies["nexuspilot_csrf"]},
    )
    key = created.json()["key"]
    client.cookies.clear()
    client.headers.pop("X-API-Key", None)
    headers = {"Authorization": "Bearer " + key}
    for mode in ("response", "agent"):
        run = (
            await client.post(
                "/api/v1/runs",
                headers=headers,
                json={"user_id": "authenticated-user", "user_request": "hello"},
            )
        ).json()
        if mode == "response":
            result = await client.post(
                "/api/v1/responses",
                headers=headers,
                json={
                    "run_id": run["run_id"],
                    "provider": "openai",
                    "model": "test-model",
                    "input": "hello",
                },
            )
            assert result.status_code == 200, result.text
        else:
            result = await client.post(
                f"/api/v1/runs/{run['run_id']}/agent-workflows",
                headers=headers,
                json=general_payload("never"),
            )
            assert result.status_code == 201, result.text
            assert result.json()["status"] == "completed", result.text
            assert result.json()["aggregate_usage"]["tool_call_count"] == 1
            workflow_id = result.json()["workflow_execution_id"]
            result_path = f"/api/v1/agent-workflows/{workflow_id}/result"
            assert (await client.get(result_path, headers=headers)).status_code == 200
        assert key not in result.text
        assert provider.server_credential not in result.text
        async with test_database_session_factory() as db:
            saved = await db.get(LlmRun, run["run_id"])
            assert saved.user_id == "authenticated-user"
            attempts = list(
                await db.scalars(
                    select(LlmModelAttempt).where(LlmModelAttempt.run_id == saved.run_id)
                )
            )
            assert attempts and all(a.provider == "openai" for a in attempts)
    assert len(provider.requests) == 3
    for request in provider.requests:
        assert key not in request.model_dump_json()
        assert provider.server_credential not in request.model_dump_json()

    # A missing model must fail before invoking the configured adapter, not select another.
    fresh_run = (
        await client.post(
            "/api/v1/runs",
            headers=headers,
            json={"user_id": "authenticated-user", "user_request": "unavailable model"},
        )
    ).json()
    unavailable = await client.post(
        "/api/v1/responses",
        headers=headers,
        json={
            "run_id": fresh_run["run_id"],
            "provider": "missing",
            "model": "missing",
            "input": "hello",
        },
    )
    assert unavailable.status_code >= 400
    assert len(provider.requests) == 3
    await provision_user(test_database_session_factory, user_id="outsider", email="out@test.com")
    await login(client, email="out@test.com")
    outsider = await client.post(
        "/api/v1/me/api-keys",
        json={"name": "other", "scopes": ["read", "execute"]},
        headers={"Origin": "http://test", "X-CSRF-Token": client.cookies["nexuspilot_csrf"]},
    )
    client.cookies.clear()
    other_headers = {"Authorization": "Bearer " + outsider.json()["key"]}
    assert (await client.get(result_path, headers=other_headers)).status_code == 404
    assert (
        await client.post(f"/api/v1/agent-workflows/{workflow_id}/cancel", headers=other_headers)
    ).status_code == 404


async def test_key_management_requires_csrf_and_owns_each_key(
    resource_client, test_database_session_factory
):
    """Reject unauthenticated mutations and another browser user's key revocation."""
    client = resource_client
    await provision_user(test_database_session_factory)
    await login(client)
    payload = {"name": "script", "scopes": ["read", "execute"]}
    assert (await client.post("/api/v1/me/api-keys", json=payload)).status_code == 403
    csrf = {"Origin": "http://test", "X-CSRF-Token": client.cookies["nexuspilot_csrf"]}
    for scopes in ([], ["admin"]):
        assert (
            await client.post(
                "/api/v1/me/api-keys", headers=csrf, json={**payload, "scopes": scopes}
            )
        ).status_code == 422
    created = (await client.post("/api/v1/me/api-keys", headers=csrf, json=payload)).json()
    path = "/api/v1/me/api-keys/" + created["api_key_id"]
    assert (await client.delete(path)).status_code == 403
    client.cookies.clear()
    await provision_user(test_database_session_factory, user_id="second", email="second@test.com")
    await login(client, email="second@test.com")
    csrf = {"Origin": "http://test", "X-CSRF-Token": client.cookies["nexuspilot_csrf"]}
    assert (await client.get("/api/v1/me/api-keys")).json() == []
    assert (await client.delete(path, headers=csrf)).status_code == 404
    client.cookies.clear()
    bearer = {"Authorization": "Bearer " + created["key"]}
    assert (
        await client.post(
            "/api/v1/runs", headers=bearer, json={"user_id": "second", "user_request": "spoof"}
        )
    ).status_code == 404


async def test_app_and_personal_identity_do_not_fallback(
    resource_client, test_database_session_factory, monkeypatch
):
    """App credentials cannot act as personal credentials and conflicting cookies are rejected."""
    import json

    from pydantic import SecretStr

    client = resource_client
    app_key = "app-service-credential-" + "x" * 32
    monkeypatch.setattr(
        get_settings(),
        "app_tools_json",
        SecretStr(
            json.dumps(
                {
                    "retail": {
                        "inbound_key": app_key,
                        "service_token": "business-private-token",
                        "base_url": "https://business.example.invalid",
                        "operations": {"retail_search_products": "/search"},
                    }
                }
            )
        ),
    )
    await provision_user(test_database_session_factory)
    await login(client)
    created = await client.post(
        "/api/v1/me/api-keys",
        json={"name": "personal", "scopes": ["read"]},
        headers={"Origin": "http://test", "X-CSRF-Token": client.cookies["nexuspilot_csrf"]},
    )
    key = created.json()["key"]
    app_headers = {
        "Authorization": "Bearer " + app_key,
        "X-Application-ID": "retail",
        "X-External-User-ID": "u1",
    }
    assert (
        await client.post("/api/v1/app/runs", headers=app_headers, json={"user_request": "test"})
    ).status_code == 400
    client.cookies.clear()
    assert (
        await client.get("/api/v1/runs", headers={"Authorization": "Bearer " + app_key})
    ).status_code == 401
    assert (
        await client.post(
            "/api/v1/app/runs",
            headers={**app_headers, "Authorization": "Bearer " + key},
            json={"user_request": "test"},
        )
    ).status_code == 401
    assert (
        await client.post("/api/v1/app/runs", headers=app_headers, json={"user_request": "test"})
    ).status_code == 201
