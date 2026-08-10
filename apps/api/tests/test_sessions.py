"""Conversation session and immutable message API behavior tests."""

import httpx


async def create_user(client: httpx.AsyncClient, user_id: str) -> None:
    """Create one active user required to own a conversation."""

    response = await client.post(
        "/api/v1/users",
        json={"user_id": user_id, "display_name": user_id},
    )
    assert response.status_code == 201


async def create_session(
    client: httpx.AsyncClient,
    user_id: str,
    title: str = "Conversation",
) -> dict:
    """Create and return one conversation through the public HTTP contract."""

    response = await client.post(
        "/api/v1/sessions",
        json={"user_id": user_id, "title": title},
    )
    assert response.status_code == 201
    return response.json()


async def append_message(
    client: httpx.AsyncClient,
    session_id: str,
    content: str,
    **extra: str,
) -> dict:
    """Append and return one text message to the selected conversation."""

    response = await client.post(
        f"/api/v1/sessions/{session_id}/messages",
        json={"role": "user", "content_text": content, **extra},
    )
    assert response.status_code == 201
    return response.json()


async def test_session_create_detail_filter_and_update(client: httpx.AsyncClient) -> None:
    """Verify conversations support ownership filtering, details, and restricted updates."""

    await create_user(client, "session-owner")
    conversation = await create_session(client, "session-owner", "Before")

    update = await client.patch(
        f"/api/v1/sessions/{conversation['session_id']}",
        json={"title": "After", "status": "archived"},
    )
    listing = await client.get(
        "/api/v1/sessions",
        params={"user_id": "session-owner", "status": "archived"},
    )
    detail = await client.get(f"/api/v1/sessions/{conversation['session_id']}")

    assert update.status_code == 200
    assert update.json()["title"] == "After"
    assert update.json()["status"] == "archived"
    assert [item["session_id"] for item in listing.json()["items"]] == [
        conversation["session_id"]
    ]
    assert detail.json()["title"] == "After"


async def test_session_list_uses_signed_cursor_without_duplicates(
    client: httpx.AsyncClient,
) -> None:
    """Verify conversation pages preserve stable order and reject altered cursors."""

    await create_user(client, "cursor-session-owner")
    sessions = [
        await create_session(client, "cursor-session-owner", title)
        for title in ["One", "Two", "Three"]
    ]

    first = (await client.get("/api/v1/sessions", params={"limit": 2})).json()
    second = (
        await client.get(
            "/api/v1/sessions",
            params={"limit": 2, "cursor": first["next_cursor"]},
        )
    ).json()
    cursor = first["next_cursor"]
    replacement = "A" if cursor[-1] != "A" else "B"
    tampered = await client.get(
        "/api/v1/sessions",
        params={"cursor": cursor[:-1] + replacement},
    )

    assert [item["session_id"] for item in first["items"]] == [
        sessions[0]["session_id"],
        sessions[1]["session_id"],
    ]
    assert [item["session_id"] for item in second["items"]] == [
        sessions[2]["session_id"]
    ]
    assert tampered.status_code == 422


async def test_session_cursor_is_bound_to_list_filters(
    client: httpx.AsyncClient,
) -> None:
    """Verify a conversation cursor cannot be replayed under different list filters."""

    await create_user(client, "session-filter-owner-a")
    await create_user(client, "session-filter-owner-b")
    for title in ["One", "Two"]:
        await create_session(client, "session-filter-owner-a", title)
    first_page = (
        await client.get(
            "/api/v1/sessions",
            params={"user_id": "session-filter-owner-a", "limit": 1},
        )
    ).json()

    changed_owner = await client.get(
        "/api/v1/sessions",
        params={
            "user_id": "session-filter-owner-b",
            "cursor": first_page["next_cursor"],
        },
    )
    changed_status = await client.get(
        "/api/v1/sessions",
        params={
            "user_id": "session-filter-owner-a",
            "status": "archived",
            "cursor": first_page["next_cursor"],
        },
    )

    assert changed_owner.status_code == 422
    assert changed_owner.json() == {"detail": "Invalid pagination cursor"}
    assert changed_status.status_code == 422
    assert changed_status.json() == {"detail": "Invalid pagination cursor"}


async def test_messages_are_immutable_and_sequence_paginated(
    client: httpx.AsyncClient,
) -> None:
    """Verify messages receive monotonic sequences and expose no mutation endpoint."""

    await create_user(client, "message-owner")
    conversation = await create_session(client, "message-owner")
    messages = [
        await append_message(client, conversation["session_id"], content)
        for content in ["First", "Second", "Third"]
    ]

    first = (
        await client.get(
            f"/api/v1/sessions/{conversation['session_id']}/messages",
            params={"limit": 2},
        )
    ).json()
    second_response = await client.get(
        f"/api/v1/sessions/{conversation['session_id']}/messages",
        params={"limit": 2, "cursor": first["next_cursor"]},
    )
    detail = await client.get(f"/api/v1/messages/{messages[1]['message_id']}")
    mutation = await client.patch(
        f"/api/v1/messages/{messages[1]['message_id']}",
        json={"content_text": "Changed"},
    )

    assert [item["sequence"] for item in first["items"]] == [1, 2]
    assert first["items"][0]["content_preview"] == "First"
    assert "content_text" not in first["items"][0]
    assert [item["sequence"] for item in second_response.json()["items"]] == [3]
    assert detail.json()["content_text"] == "Second"
    assert mutation.status_code == 405


async def test_full_message_pages_return_latest_window_and_complete_bodies(
    client: httpx.AsyncClient,
) -> None:
    """Verify one full-body page restores the newest window and older cursor pages."""

    await create_user(client, "latest-message-owner")
    conversation = await create_session(client, "latest-message-owner")
    for index in range(105):
        await append_message(client, conversation["session_id"], f"message-{index}")

    latest = await client.get(
        f"/api/v1/sessions/{conversation['session_id']}/messages/latest",
        params={"limit": 100},
    )
    older = await client.get(
        f"/api/v1/sessions/{conversation['session_id']}/messages/latest",
        params={"limit": 100, "cursor": latest.json()["next_cursor"]},
    )

    assert latest.status_code == 200
    assert [item["sequence"] for item in latest.json()["items"]] == list(range(6, 106))
    assert latest.json()["items"][0]["content_text"] == "message-5"
    assert "content_preview" not in latest.json()["items"][0]
    assert older.status_code == 200
    assert [item["sequence"] for item in older.json()["items"]] == list(range(1, 6))
    assert older.json()["has_more"] is False


async def test_latest_run_detail_restores_the_newest_session_run(
    client: httpx.AsyncClient,
) -> None:
    """Verify the session restore endpoint returns the newest run fact set."""

    await create_user(client, "latest-run-owner")
    conversation = await create_session(client, "latest-run-owner")
    first = await client.post(
        "/api/v1/runs",
        json={
            "user_id": "latest-run-owner",
            "session_id": conversation["session_id"],
            "user_request": "first",
        },
    )
    second = await client.post(
        "/api/v1/runs",
        json={
            "user_id": "latest-run-owner",
            "session_id": conversation["session_id"],
            "user_request": "second",
        },
    )

    latest = await client.get(
        f"/api/v1/sessions/{conversation['session_id']}/latest-run",
    )

    assert first.status_code == 201
    assert second.status_code == 201
    assert latest.status_code == 200
    assert latest.json()["run_id"] == second.json()["run_id"]
    assert "attempts" in latest.json()
    assert "cost_used" in latest.json()


async def test_message_cursor_is_bound_to_its_session(client: httpx.AsyncClient) -> None:
    """Verify a sequence cursor cannot silently skip rows in another conversation."""

    await create_user(client, "scoped-cursor-owner")
    first_session = await create_session(client, "scoped-cursor-owner", "First")
    second_session = await create_session(client, "scoped-cursor-owner", "Second")
    for content in ["One", "Two"]:
        await append_message(client, first_session["session_id"], content)
        await append_message(client, second_session["session_id"], content)
    first_page = (
        await client.get(
            f"/api/v1/sessions/{first_session['session_id']}/messages",
            params={"limit": 1},
        )
    ).json()

    replay = await client.get(
        f"/api/v1/sessions/{second_session['session_id']}/messages",
        params={"cursor": first_page["next_cursor"]},
    )

    assert replay.status_code == 422
    assert replay.json() == {"detail": "Invalid pagination cursor"}


async def test_archived_session_rejects_new_messages(client: httpx.AsyncClient) -> None:
    """Verify archived conversations retain history but reject new writes."""

    await create_user(client, "archived-owner")
    conversation = await create_session(client, "archived-owner")
    await client.patch(
        f"/api/v1/sessions/{conversation['session_id']}",
        json={"status": "archived"},
    )

    response = await client.post(
        f"/api/v1/sessions/{conversation['session_id']}/messages",
        json={"role": "user", "content_text": "Late message"},
    )

    assert response.status_code == 409
    assert response.json() == {"detail": "Cannot append a message to an archived session"}


async def test_message_content_requires_exactly_one_source(client: httpx.AsyncClient) -> None:
    """Verify messages cannot be empty or ambiguously backed by two content sources."""

    await create_user(client, "content-owner")
    conversation = await create_session(client, "content-owner")
    endpoint = f"/api/v1/sessions/{conversation['session_id']}/messages"

    empty = await client.post(endpoint, json={"role": "user"})
    duplicate = await client.post(
        endpoint,
        json={
            "role": "user",
            "content_text": "Inline",
            "content_uri": "memory://content/object",
        },
    )

    assert empty.status_code == 422
    assert duplicate.status_code == 422


async def test_run_must_use_a_session_owned_by_the_same_user(
    client: httpx.AsyncClient,
) -> None:
    """Verify run creation enforces conversation ownership and existence."""

    await create_user(client, "run-owner-a")
    await create_user(client, "run-owner-b")
    conversation = await create_session(client, "run-owner-a")

    response = await client.post(
        "/api/v1/runs",
        json={
            "user_id": "run-owner-b",
            "session_id": conversation["session_id"],
            "user_request": "Should fail",
        },
    )

    assert response.status_code == 422
    assert response.json() == {"detail": "Run session must belong to the run user"}


async def test_message_run_and_parent_must_belong_to_the_same_session(
    client: httpx.AsyncClient,
) -> None:
    """Verify message lineage cannot cross its owning conversation boundary."""

    await create_user(client, "lineage-owner")
    first_session = await create_session(client, "lineage-owner", "First")
    second_session = await create_session(client, "lineage-owner", "Second")
    run_response = await client.post(
        "/api/v1/runs",
        json={
            "user_id": "lineage-owner",
            "session_id": first_session["session_id"],
            "user_request": "Linked run",
        },
    )
    assert run_response.status_code == 201
    first_message = await append_message(
        client,
        first_session["session_id"],
        "First message",
        run_id=run_response.json()["run_id"],
    )

    wrong_run = await client.post(
        f"/api/v1/sessions/{second_session['session_id']}/messages",
        json={
            "role": "assistant",
            "content_text": "Wrong run",
            "run_id": run_response.json()["run_id"],
        },
    )
    wrong_parent = await client.post(
        f"/api/v1/sessions/{second_session['session_id']}/messages",
        json={
            "role": "assistant",
            "content_text": "Wrong parent",
            "parent_message_id": first_message["message_id"],
        },
    )

    assert wrong_run.status_code == 409
    assert wrong_run.json() == {"detail": "Run does not belong to session"}
    assert wrong_parent.status_code == 409
    assert wrong_parent.json() == {"detail": "Parent message does not belong to session"}
