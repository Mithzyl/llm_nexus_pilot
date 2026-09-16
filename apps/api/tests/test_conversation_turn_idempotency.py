"""Verify persistent turn retries independently of subsequent model execution."""

import pytest
from test_sessions import create_session, create_user


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
