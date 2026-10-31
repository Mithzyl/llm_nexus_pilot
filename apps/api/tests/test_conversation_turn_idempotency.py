"""Verify persistent turn retries independently of subsequent model execution."""

import pytest
from test_sessions import create_session, create_user


async def test_session_creation_key_survives_lost_response_and_rename(client):
    """Retrying initial session creation reuses its identity and rejects a changed request."""
    await create_user(client, "session-recovery-owner")
    payload = {"user_id": "session-recovery-owner", "title": "original",
               "idempotency_key": "session-create-key-0001"}
    first = await client.post("/api/v1/sessions", json=payload)
    assert first.status_code == 201
    session_id = first.json()["session_id"]
    await client.patch(f"/api/v1/sessions/{session_id}", json={"title": "renamed"})
    again = await client.post("/api/v1/sessions", json=payload)
    assert again.status_code == 201
    assert again.json()["session_id"] == session_id
    assert again.json()["title"] == "renamed"
    conflict = await client.post("/api/v1/sessions", json={**payload, "title": "changed input"})
    assert conflict.status_code == 409


async def test_real_mysql_session_creation_race(client, test_database_session_factory):
    """Concurrent retries use the actual MySQL owner lock to create one conversation."""
    import asyncio
    if test_database_session_factory.kw["bind"].dialect.name != "mysql":
        pytest.skip("Requires isolated real MySQL fixture")
    await create_user(client, "session-race-owner")
    responses = await asyncio.gather(*(
        client.post("/api/v1/sessions", json={"user_id": "session-race-owner",
            "title": "same", "idempotency_key": "concurrent-key-001"}) for _ in range(2)
    ))
    assert all(response.status_code == 201 for response in responses)
    assert len({response.json()["session_id"] for response in responses}) == 1


async def test_find_committed_turn_without_reposting_prompt(client):
    """A lost creation response can be discovered by the original key without new writes."""
    await create_user(client, "discovery-owner")
    session = await create_session(client, "discovery-owner")
    endpoint = f"/api/v1/sessions/{session['session_id']}/turns"
    key = "discovery-key-0001"
    assert (await client.get(f"{endpoint}/{key}")).status_code == 404
    created = await client.post(endpoint, json={"user_id": "discovery-owner",
        "content_text": "original request", "idempotency_key": key})
    assert created.status_code == 201
    found = await client.get(f"{endpoint}/{key}")
    assert found.status_code == 200
    assert found.json() == created.json()
    other_session = await create_session(client, "discovery-owner")
    assert (await client.get(
        f"/api/v1/sessions/{other_session['session_id']}/turns/{key}",
    )).status_code == 404
    messages = await client.get(f"/api/v1/sessions/{session['session_id']}/messages")
    assert len(messages.json()["items"]) == 1


async def test_discovery_checks_logged_in_owner(resource_client, test_database_session_factory,
                                               monkeypatch):
    """Knowing another user's submission key does not authorize discovery of their Run."""
    from test_authentication import login, provision_user

    from nexuspilot_api.core.config import get_settings
    monkeypatch.setenv("NEXUSPILOT_AUTHENTICATION_ENABLED", "true")
    get_settings.cache_clear()
    try:
        for user in ("discovery-a", "discovery-b"):
            await provision_user(test_database_session_factory, user_id=user,
                                 email=f"{user}@example.test")
        assert (await login(resource_client, email="discovery-a@example.test")).status_code == 200
        resource_client.headers.update({"Origin": "http://test",
            "X-CSRF-Token": resource_client.cookies["nexuspilot_csrf"]})
        session = await resource_client.post("/api/v1/sessions", json={"user_id": "discovery-a"})
        assert session.status_code == 201
        endpoint = f"/api/v1/sessions/{session.json()['session_id']}/turns"
        created = await resource_client.post(endpoint, json={"user_id": "discovery-a",
            "content_text": "private", "idempotency_key": "private-key-01"})
        assert created.status_code == 201
        assert (await resource_client.get(endpoint + "/private-key-01")).status_code == 200
        resource_client.cookies.clear()
        resource_client.headers.pop("X-CSRF-Token", None)
        assert (await login(resource_client, email="discovery-b@example.test")).status_code == 200
        assert (await resource_client.get(endpoint + "/private-key-01")).status_code == 404
    finally:
        get_settings.cache_clear()


async def test_same_turn_key_reuses_run_and_message(client):
    """An acknowledged or lost-response retry must not append another message or Run."""

    await create_user(client, "turn-retry-owner")
    conversation = await create_session(client, "turn-retry-owner")
    endpoint = f"/api/v1/sessions/{conversation['session_id']}/turns"
    payload = {
        "user_id": "turn-retry-owner",
        "content_text": "同一次提交",
        "idempotency_key": "turn-retry-0001",
    }
    first = await client.post(endpoint, json=payload)
    second = await client.post(endpoint, json=payload)
    assert first.status_code == 201
    assert second.status_code == 200
    assert first.json()["run"]["run_id"] == second.json()["run"]["run_id"]
    assert first.json()["message"]["message_id"] == second.json()["message"]["message_id"]
    messages = await client.get(f"/api/v1/sessions/{conversation['session_id']}/messages")
    assert len(messages.json()["items"]) == 1


@pytest.mark.parametrize(
    "change", [{"content_text": "different"}, {"run_type": "other"}, {"budget_limit": "1"}]
)
async def test_same_turn_key_rejects_changed_request(client, change):
    """A stable key cannot silently reuse a different question or execution budget."""

    await create_user(client, "turn-conflict-owner")
    conversation = await create_session(client, "turn-conflict-owner")
    endpoint = f"/api/v1/sessions/{conversation['session_id']}/turns"
    payload = {
        "user_id": "turn-conflict-owner",
        "content_text": "original",
        "idempotency_key": "turn-conflict-0001",
    }
    assert (await client.post(endpoint, json=payload)).status_code == 201
    assert (await client.post(endpoint, json={**payload, **change})).status_code == 409
    messages = await client.get(f"/api/v1/sessions/{conversation['session_id']}/messages")
    assert len(messages.json()["items"]) == 1


async def test_turn_keys_are_session_scoped_and_never_bypass_owner(client):
    """The same key may identify different sessions, but a replay still checks ownership."""

    await create_user(client, "turn-scope-owner")
    await create_user(client, "turn-other-owner")
    sessions = [await create_session(client, "turn-scope-owner") for _ in range(2)]
    payload = {
        "user_id": "turn-scope-owner",
        "content_text": "question",
        "idempotency_key": "turn-scope-0001",
    }
    runs = []
    for conversation in sessions:
        response = await client.post(
            f"/api/v1/sessions/{conversation['session_id']}/turns", json=payload
        )
        assert response.status_code == 201
        runs.append(response.json()["run"]["run_id"])
    assert runs[0] != runs[1]
    denied = await client.post(
        f"/api/v1/sessions/{sessions[0]['session_id']}/turns",
        json={**payload, "user_id": "turn-other-owner"},
    )
    assert denied.status_code == 422


async def test_unkeyed_legacy_turns_remain_distinct(client):
    """Legacy clients without a key keep creating explicit separate turns."""

    await create_user(client, "turn-legacy-owner")
    conversation = await create_session(client, "turn-legacy-owner")
    endpoint = f"/api/v1/sessions/{conversation['session_id']}/turns"
    payload = {"user_id": "turn-legacy-owner", "content_text": "repeat on purpose"}
    first = await client.post(endpoint, json=payload)
    second = await client.post(endpoint, json=payload)
    assert first.status_code == second.status_code == 201
    assert first.json()["run"]["run_id"] != second.json()["run"]["run_id"]


async def test_new_key_waits_for_previous_turn_but_same_key_recovers(client):
    """A stale second tab cannot append a new keyed turn while the first awaits execution."""
    await create_user(client, "occupied-owner")
    conversation = await create_session(client, "occupied-owner")
    endpoint = f"/api/v1/sessions/{conversation['session_id']}/turns"
    payload = {"user_id": "occupied-owner", "content_text": "first",
               "idempotency_key": "occupied-first-key"}
    first = await client.post(endpoint, json=payload)
    assert first.status_code == 201
    assert (await client.post(endpoint, json=payload)).status_code == 200
    second = {**payload, "content_text": "second", "idempotency_key": "occupied-second-key"}
    assert (await client.post(endpoint, json=second)).status_code == 409
    assert (await client.post(f"/api/v1/runs/{first.json()['run']['run_id']}/cancel")).status_code == 200
    assert (await client.post(endpoint, json=second)).status_code == 201
