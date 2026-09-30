"""Exercise the real authorization dependency across the full HTTP policy inventory."""

import asyncio
import re

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import func, select
from test_authentication import login, provision_user

from nexuspilot_api.core.config import get_settings
from nexuspilot_api.features.identity.resource_access import RESOURCES
from nexuspilot_api.features.identity.resource_policy import (
    INTERNAL_ROUTES,
    OWNED_ROUTES,
    SHARED_ROUTES,
)
from nexuspilot_api.main import app
from nexuspilot_api.models import LlmMessage, LlmModelAttempt, LlmRun, LlmTask, User


@pytest.fixture(autouse=True)
def enabled_authentication(monkeypatch):
    """Enable actual session authorization and restore cached settings after each test."""
    monkeypatch.setenv("NEXUSPILOT_AUTHENTICATION_ENABLED", "true")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def concrete_path(path):
    """Substitute syntactically valid locators; missing-login rejection must precede lookup."""
    return "/api/v1" + re.sub(r"\{[^}]+\}", "00000000-0000-0000-0000-000000000001", path)


def test_all_business_methods_have_exactly_one_policy():
    """Adding an unclassified or multiply classified method fails the default test suite."""
    actual = {
        (method.upper(), path.removeprefix("/api/v1"))
        for path, operations in app.openapi()["paths"].items()
        if path.startswith("/api/v1/") and not path.startswith("/api/v1/auth/")
        for method in operations
        if method in {"get", "post", "patch", "put", "delete"}
    }
    assert actual == INTERNAL_ROUTES | OWNED_ROUTES | SHARED_ROUTES
    assert not INTERNAL_ROUTES & OWNED_ROUTES
    assert not INTERNAL_ROUTES & SHARED_ROUTES
    assert not OWNED_ROUTES & SHARED_ROUTES
    for _, path in OWNED_ROUTES:
        assert set(re.findall(r"\{([^}]+)\}", path)) <= RESOURCES.keys(), path


async def test_all_methods_reject_service_key_without_required_identity(resource_client):
    """Check every classified method rejects missing user or internal-service credentials."""
    for method, path in sorted(INTERNAL_ROUTES | OWNED_ROUTES | SHARED_ROUTES):
        response = await resource_client.request(
            method, concrete_path(path), json={} if method != "GET" else None
        )
        assert response.status_code == 401, (method, path, response.status_code, response.text)


async def test_internal_key_cannot_elevate_a_browser_cookie(resource_client):
    """Internal service credentials cannot upgrade any browser request into management access."""
    resource_client.cookies.set("nexuspilot_session", "browser-cookie-must-not-be-admin")
    for method, path in sorted(INTERNAL_ROUTES):
        response = await resource_client.request(
            method,
            concrete_path(path),
            headers={"X-Internal-API-Key": "test-internal-key-long-enough"},
            json={} if method != "GET" else None,
        )
        assert response.status_code == 403, (method, path, response.status_code)


@pytest_asyncio.fixture
async def two_users(resource_client, test_database_session_factory):
    """Provision two real identities and create each one's sessions, messages and runs via HTTP."""
    owners = []
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"X-API-Key": get_settings().api_key},
    ) as second:
        for client, user_id in [(resource_client, "policy-a"), (second, "policy-b")]:
            await provision_user(
                test_database_session_factory, user_id=user_id, email=f"{user_id}@example.test"
            )
            assert (await login(client, email=f"{user_id}@example.test")).status_code == 200
            client.headers.update(
                {"Origin": "http://test", "X-CSRF-Token": client.cookies["nexuspilot_csrf"]}
            )
            sessions = []
            for index in range(3):
                session = await client.post(
                    "/api/v1/sessions", json={"user_id": user_id, "title": f"{user_id}-{index}"}
                )
                assert session.status_code == 201
                sessions.append(session.json())
            sid = sessions[0]["session_id"]
            turn = await client.post(
                f"/api/v1/sessions/{sid}/turns",
                json={
                    "user_id": user_id,
                    "content_text": "Private turn",
                    "idempotency_key": f"turn-{user_id}",
                },
            )
            assert turn.status_code == 201, turn.text
            run_id = turn.json()["run"]["run_id"]
            task = await client.post(
                f"/api/v1/runs/{run_id}/tasks",
                json={"task_type": "test", "title": "Task", "objective": "Private task"},
            )
            assert task.status_code == 201
            artifact = await client.post(
                f"/api/v1/runs/{run_id}/artifacts",
                data={"artifact_type": "test"},
                files={"file": ("private.txt", user_id.encode(), "text/plain")},
            )
            assert artifact.status_code == 201
            owners.append(
                {
                    "client": client,
                    "user": user_id,
                    "sessions": sessions,
                    "run": run_id,
                    "message": turn.json()["message"]["message_id"],
                    "task": task.json()["task_id"],
                    "artifact": artifact.json()["artifact_id"],
                }
            )
        yield owners


async def test_lists_and_cursors_are_owner_bound(two_users):
    """Both directions filter global lists in SQL and reject another owner's signed cursor."""
    for own, other in (two_users, list(reversed(two_users))):
        client = own["client"]
        for path, field, forbidden in [
            ("users", "user_id", other["user"]),
            ("sessions", "session_id", other["sessions"][0]["session_id"]),
            ("runs", "run_id", other["run"]),
            ("tasks", "task_id", other["task"]),
            (f"artifacts?run_id={own['run']}", "artifact_id", other["artifact"]),
        ]:
            response = await client.get(f"/api/v1/{path}")
            assert response.status_code == 200, (path, response.text)
            assert response.json()["items"]
            assert forbidden not in [item[field] for item in response.json()["items"]]
        page = await client.get("/api/v1/sessions?limit=1")
        cursor = page.json()["next_cursor"]
        assert cursor
        assert (
            await client.get("/api/v1/sessions", params={"limit": 1, "cursor": cursor})
        ).status_code == 200
        assert (
            await other["client"].get("/api/v1/sessions", params={"limit": 1, "cursor": cursor})
        ).status_code == 422


async def test_nested_references_and_replay_reject_before_business_writes(
    two_users, test_database_session_factory
):
    """Reject mixed owners, conflicting URL/body IDs and replay before any new model or DB facts."""

    async def counts():
        """Count relevant persisted facts without relying on response status alone."""
        async with test_database_session_factory() as db:
            return [
                await db.scalar(select(func.count()).select_from(model))
                for model in (LlmRun, LlmTask, LlmMessage, LlmModelAttempt)
            ]

    before = await counts()
    for own, other in (two_users, list(reversed(two_users))):
        client = own["client"]
        payload = {
            "run_id": own["run"],
            "current_user_message_id": own["message"],
            "provider": "openai",
            "model": "test-model",
            "input": "Read",
        }
        for refs in [
            {"artifact_ids": [own["artifact"], other["artifact"]]},
            {"parsed_artifacts": [{"artifact_id": other["artifact"]}]},
        ]:
            response = await client.post(
                "/api/v1/responses", json={**payload, "context_source_references": refs}
            )
            assert response.status_code == 404, response.text
        assert (
            await client.post(
                f"/api/v1/runs/{own['run']}/tasks",
                json={
                    "task_type": "test",
                    "title": "Task",
                    "objective": "Private task",
                    "depends_on_task_ids": [other["task"]],
                },
            )
        ).status_code == 404
        assert (
            await client.post(
                f"/api/v1/sessions/{other['sessions'][0]['session_id']}/turns",
                json={
                    "user_id": other["user"],
                    "content_text": "Private turn",
                    "idempotency_key": f"turn-{other['user']}",
                },
            )
        ).status_code == 404
        assert (
            await client.post(f"/api/v1/runs/{other['run']}/cancel", json={"run_id": own["run"]})
        ).status_code == 404
        assert (
            await client.get(
                "/api/v1/runs", params=[("user_id", other["user"]), ("user_id", own["user"])]
            )
        ).status_code == 404
        assert (
            await client.post(
                f"/api/v1/sessions/{own['sessions'][0]['session_id']}/messages",
                json={"role": "user", "content_uri": "minio://private/other-object"},
            )
        ).status_code == 403
    assert await counts() == before


async def test_all_user_mutations_require_csrf_before_resource_lookup(two_users):
    """Apply session-bound CSRF to every final-user write, not only file endpoints."""
    client = two_users[0]["client"]
    client.headers.pop("X-CSRF-Token")
    for method, path in sorted(OWNED_ROUTES):
        if method == "GET":
            continue
        response = await client.request(method, concrete_path(path), json={})
        assert response.status_code == 403, (method, path, response.status_code)


@pytest.mark.parametrize("revoke", ["logout", "disabled"])
async def test_revoked_or_disabled_identity_cannot_access_business_resources(
    two_users, test_database_session_factory, revoke
):
    """Re-check stored session and user state on each new resource request."""
    own = two_users[0]
    if revoke == "logout":
        cookies = httpx.Cookies(own["client"].cookies)
        assert (await own["client"].post("/api/v1/auth/logout")).status_code == 204
        own["client"].cookies.update(cookies)
    else:
        async with test_database_session_factory() as db:
            user = await db.get(User, own["user"])
            user.is_active = False
            await db.commit()
    response = await own["client"].get(f"/api/v1/sessions/{own['sessions'][0]['session_id']}")
    assert response.status_code == 401


async def test_local_mode_cannot_select_arbitrary_owner(
    resource_client, monkeypatch, test_database_session_factory
):
    """Local mode resolves only its reserved owner, not a caller-provided user ID."""
    monkeypatch.setenv("NEXUSPILOT_AUTHENTICATION_ENABLED", "false")
    get_settings.cache_clear()
    await provision_user(
        test_database_session_factory, user_id="foreign", email="foreign@example.test"
    )
    principal = (await resource_client.get("/api/v1/auth/me")).json()
    assert principal["user_id"] == "nexuspilot-web"
    assert (
        await resource_client.post("/api/v1/sessions", json={"user_id": "foreign"})
    ).status_code == 404
    assert (
        await resource_client.post("/api/v1/sessions", json={"user_id": "nexuspilot-web"})
    ).status_code == 201


async def test_concurrent_requests_do_not_share_owner_state(two_users):
    """Independent SQL sessions retain their own identity during concurrent list requests."""
    async def read_owned(owner):
        """Assert every returned row belongs to the browser issuing this request."""
        response = await owner["client"].get("/api/v1/sessions")
        assert response.status_code == 200
        assert len(response.json()["items"]) == 3
        assert all(item["user_id"] == owner["user"] for item in response.json()["items"])

    await asyncio.gather(*(read_owned(owner) for _ in range(6) for owner in two_users))
