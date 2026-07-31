"""User resource tests for details, updates, filtering, and signed cursor pagination."""

import httpx


async def create_user(
    client: httpx.AsyncClient,
    user_id: str,
    display_name: str | None = None,
) -> dict:
    """Create and return one user through the public HTTP contract."""

    response = await client.post(
        "/api/v1/users",
        json={"user_id": user_id, "display_name": display_name or user_id},
    )
    assert response.status_code == 201
    return response.json()


async def test_user_detail_and_restricted_update(client: httpx.AsyncClient) -> None:
    """Verify callers can read and update only the declared mutable user fields."""

    await create_user(client, "managed-user", "Before")
    update = await client.patch(
        "/api/v1/users/managed-user",
        json={"display_name": "After", "is_active": False},
    )

    assert update.status_code == 200
    assert update.json()["display_name"] == "After"
    assert update.json()["is_active"] is False
    detail = await client.get("/api/v1/users/managed-user")
    assert detail.status_code == 200
    assert detail.json()["user_id"] == "managed-user"
    assert detail.json()["is_active"] is False


async def test_user_update_rejects_empty_or_unknown_fields(client: httpx.AsyncClient) -> None:
    """Verify generic PATCH cannot mutate primary keys or submit an empty update."""

    await create_user(client, "restricted-user")

    assert (await client.patch("/api/v1/users/restricted-user", json={})).status_code == 422
    response = await client.patch(
        "/api/v1/users/restricted-user",
        json={"user_id": "different-user"},
    )
    assert response.status_code == 422
    detail = await client.get("/api/v1/users/restricted-user")
    assert detail.json()["user_id"] == "restricted-user"


async def test_user_detail_returns_not_found(client: httpx.AsyncClient) -> None:
    """Verify an unknown user produces the stable application-level 404 response."""

    response = await client.get("/api/v1/users/missing-user")

    assert response.status_code == 404
    assert response.json() == {"detail": "User not found"}


async def test_user_list_uses_stable_cursor_without_duplicates(
    client: httpx.AsyncClient,
) -> None:
    """Verify consecutive cursor pages preserve order and never repeat a user."""

    for user_id in ["cursor-a", "cursor-b", "cursor-c"]:
        await create_user(client, user_id)

    first = (await client.get("/api/v1/users", params={"limit": 2})).json()
    second_response = await client.get(
        "/api/v1/users",
        params={"limit": 2, "cursor": first["next_cursor"]},
    )
    second = second_response.json()

    assert [item["user_id"] for item in first["items"]] == ["cursor-a", "cursor-b"]
    assert first["has_more"] is True
    assert first["next_cursor"]
    assert second_response.status_code == 200
    assert [item["user_id"] for item in second["items"]] == ["cursor-c"]
    assert second["has_more"] is False
    assert second["next_cursor"] is None


async def test_user_list_filters_active_state(client: httpx.AsyncClient) -> None:
    """Verify active-state filtering is applied before cursor pagination."""

    await create_user(client, "active-user")
    await create_user(client, "inactive-user")
    await client.patch("/api/v1/users/inactive-user", json={"is_active": False})

    response = await client.get("/api/v1/users", params={"is_active": "false"})

    assert response.status_code == 200
    assert [item["user_id"] for item in response.json()["items"]] == ["inactive-user"]


async def test_user_list_rejects_tampered_cursor(client: httpx.AsyncClient) -> None:
    """Verify clients cannot alter cursor positions without invalidating the signature."""

    for user_id in ["signed-a", "signed-b"]:
        await create_user(client, user_id)
    page = (await client.get("/api/v1/users", params={"limit": 1})).json()
    cursor = page["next_cursor"]
    replacement = "A" if cursor[-1] != "A" else "B"

    response = await client.get(
        "/api/v1/users",
        params={"cursor": cursor[:-1] + replacement},
    )

    assert response.status_code == 422
    assert response.json() == {"detail": "Invalid pagination cursor"}


async def test_user_list_enforces_limit_bounds(client: httpx.AsyncClient) -> None:
    """Verify callers cannot request empty or unbounded user pages."""

    assert (await client.get("/api/v1/users", params={"limit": 0})).status_code == 422
    assert (await client.get("/api/v1/users", params={"limit": 101})).status_code == 422
