"""Authentication contract tests for provisioned users and browser sessions."""

from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from nexuspilot_api.core.config import get_settings
from nexuspilot_api.features.identity.models.authentication import (
    UserLoginSession,
    UserPasswordCredential,
)
from nexuspilot_api.features.identity.services.authentication_service import (
    provision_local_password_user,
)
from nexuspilot_api.main import app
from nexuspilot_api.models.base import utc_now

LOGIN_ORIGIN = "http://test"
TEST_PASSWORD = "correct horse battery staple"


@pytest.fixture(autouse=True)
def enable_browser_authentication(monkeypatch: pytest.MonkeyPatch):
    """Exercise opt-in authentication explicitly, independently of local defaults."""

    monkeypatch.setenv("NEXUSPILOT_AUTHENTICATION_ENABLED", "true")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def test_local_mode_uses_one_owner_without_creating_login_sessions(
    client: httpx.AsyncClient,
    test_database_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Allow local access only through the service key, never a client-selected owner."""

    monkeypatch.setenv("NEXUSPILOT_AUTHENTICATION_ENABLED", "false")
    get_settings.cache_clear()
    for _ in range(2):
        response = await client.get("/api/v1/auth/me?user_id=another-user")
        assert response.status_code == 200
        assert response.json()["user_id"] == "nexuspilot-web"
        assert response.json()["authentication_method"] == "local_deployment"
        assert response.json()["login_session_id"] is None
        assert "set-cookie" not in response.headers
    assert (await client.get("/api/v1/auth/me", headers={"X-API-Key": "wrong"})).status_code == 401
    assert (await login(client)).status_code == 403
    assert (await client.get("/api/v1/auth/sessions")).status_code == 403
    logout_response = await client.post("/api/v1/auth/logout", headers={"Origin": LOGIN_ORIGIN})
    assert logout_response.status_code == 403
    async with test_database_session_factory() as database_session:
        assert await database_session.scalar(select(UserLoginSession)) is None
    disabled = await client.patch("/api/v1/users/nexuspilot-web", json={"is_active": False})
    assert disabled.status_code == 200
    assert (await client.get("/api/v1/auth/me")).status_code == 401


async def test_local_mode_never_claims_a_password_account(
    client: httpx.AsyncClient,
    test_database_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail explicitly when the reserved local owner has been bound to a login identity."""

    await provision_user(test_database_session_factory, user_id="nexuspilot-web")
    monkeypatch.setenv("NEXUSPILOT_AUTHENTICATION_ENABLED", "false")
    get_settings.cache_clear()
    assert (await client.get("/api/v1/auth/me")).status_code == 409


async def test_invalid_login_payload_never_echoes_password(client: httpx.AsyncClient) -> None:
    """Reject malformed authentication payloads without echoing secret validation input."""

    secret = "sensitive-value-that-must-not-be-echoed"
    response = await client.post(
        "/api/v1/auth/login",
        headers={"Origin": LOGIN_ORIGIN},
        json={"email": "user@example.com", "password": {"secret": secret}},
    )
    assert response.status_code == 422
    assert secret not in response.text
    assert response.headers["cache-control"] == "no-store"


async def provision_user(
    database_session_factory: async_sessionmaker[AsyncSession],
    *,
    user_id: str = "authenticated-user",
    email: str = "User@Example.com",
    password: str = TEST_PASSWORD,
) -> None:
    """Create one local-password user through the trusted provisioning service."""

    async with database_session_factory() as database_session:
        await provision_local_password_user(
            database_session,
            user_id=user_id,
            display_name="Authenticated User",
            email=email,
            password=password,
        )


async def login(
    client: httpx.AsyncClient,
    *,
    email: str = "user@example.com",
    password: str = TEST_PASSWORD,
) -> httpx.Response:
    """Submit one same-origin browser login request."""

    return await client.post(
        "/api/v1/auth/login",
        headers={"Origin": LOGIN_ORIGIN},
        json={"email": email, "password": password, "device_name": "Test browser"},
    )


async def test_login_creates_hashed_session_and_returns_current_principal(
    client: httpx.AsyncClient,
    test_database_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Verify valid credentials create opaque cookies without persisting either secret."""

    await provision_user(test_database_session_factory)

    response = await login(client)

    assert response.status_code == 200
    assert response.json() == {
        "user_id": "authenticated-user",
        "display_name": "Authenticated User",
        "authentication_method": "browser_session",
        "login_session_id": response.json()["login_session_id"],
        "scopes": ["model:invoke", "sessions:read", "sessions:write"],
    }
    set_cookie = response.headers.get_list("set-cookie")
    assert any("nexuspilot_session=" in value and "HttpOnly" in value for value in set_cookie)
    assert any("nexuspilot_csrf=" in value and "HttpOnly" not in value for value in set_cookie)
    session_cookie = client.cookies.get("nexuspilot_session")
    csrf_cookie = client.cookies.get("nexuspilot_csrf")
    assert session_cookie
    assert csrf_cookie

    async with test_database_session_factory() as database_session:
        password_credential = await database_session.scalar(select(UserPasswordCredential))
        login_session = await database_session.scalar(select(UserLoginSession))
        assert password_credential is not None
        assert TEST_PASSWORD not in password_credential.password_hash
        assert login_session is not None
        assert session_cookie not in login_session.session_token_hash
        assert csrf_cookie not in login_session.csrf_token_hash

    current = await client.get("/api/v1/auth/me")
    assert current.status_code == 200
    assert current.json()["user_id"] == "authenticated-user"
    client.cookies.set("nexuspilot_csrf", "forged-csrf")
    forged_proof = await client.get(
        "/api/v1/auth/me", headers={"X-CSRF-Token": "forged-csrf"}
    )
    assert forged_proof.status_code == 403


async def test_unknown_email_and_wrong_password_share_one_public_failure(
    client: httpx.AsyncClient,
    test_database_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Verify login failures do not reveal whether an email identity exists."""

    await provision_user(test_database_session_factory)

    wrong_password = await login(client, password="this password is definitely wrong")
    unknown_email = await login(client, email="missing@example.com")

    assert wrong_password.status_code == 401
    assert unknown_email.status_code == 401
    assert wrong_password.json() == unknown_email.json() == {
        "detail": "邮箱或密码不正确。"
    }


async def test_current_principal_rejects_missing_expired_and_conflicting_identity(
    client: httpx.AsyncClient,
    test_database_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Verify missing, expired, or ambiguous browser identities cannot authenticate."""

    assert (await client.get("/api/v1/auth/me")).status_code == 401
    await provision_user(test_database_session_factory)
    assert (await login(client)).status_code == 200

    conflicting = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": "Bearer np_not-enabled"},
    )
    assert conflicting.status_code == 400

    async with test_database_session_factory() as database_session:
        login_session = await database_session.scalar(select(UserLoginSession))
        assert login_session is not None
        login_session.idle_expires_at = utc_now() - timedelta(seconds=1)
        await database_session.commit()

    assert (await client.get("/api/v1/auth/me")).status_code == 401


async def test_logout_requires_csrf_and_is_idempotent(
    client: httpx.AsyncClient,
    test_database_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Verify state-changing Cookie requests require the bound CSRF token."""

    await provision_user(test_database_session_factory)
    assert (await login(client)).status_code == 200
    csrf_token = client.cookies.get("nexuspilot_csrf")
    assert csrf_token

    missing_csrf = await client.post(
        "/api/v1/auth/logout",
        headers={"Origin": LOGIN_ORIGIN},
    )
    assert missing_csrf.status_code == 403
    assert (await client.get("/api/v1/auth/me")).status_code == 200

    logged_out = await client.post(
        "/api/v1/auth/logout",
        headers={"Origin": LOGIN_ORIGIN, "X-CSRF-Token": csrf_token},
    )
    assert logged_out.status_code == 204
    assert (await client.get("/api/v1/auth/me")).status_code == 401
    repeated = await client.post(
        "/api/v1/auth/logout",
        headers={"Origin": LOGIN_ORIGIN},
    )
    assert repeated.status_code == 204


async def test_revoke_all_sessions_invalidates_every_browser_session(
    client: httpx.AsyncClient,
    test_database_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Verify a security revocation immediately invalidates all user sessions."""

    await provision_user(test_database_session_factory)
    assert (await login(client)).status_code == 200
    second_transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(
        transport=second_transport,
        base_url="http://test",
        headers={"X-API-Key": "test-api-key-long-enough"},
    ) as second_client:
        assert (await login(second_client)).status_code == 200
        csrf_token = client.cookies.get("nexuspilot_csrf")
        assert csrf_token
        response = await client.post(
            "/api/v1/auth/sessions/revoke-all",
            headers={"Origin": LOGIN_ORIGIN, "X-CSRF-Token": csrf_token},
        )
        assert response.status_code == 204
        assert (await client.get("/api/v1/auth/me")).status_code == 401
        assert (await second_client.get("/api/v1/auth/me")).status_code == 401


async def test_inactive_user_cannot_log_in_or_reuse_an_existing_session(
    client: httpx.AsyncClient,
    test_database_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Verify deactivation blocks new and previously established browser sessions."""

    await provision_user(test_database_session_factory)
    assert (await login(client)).status_code == 200
    response = await client.patch(
        "/api/v1/users/authenticated-user",
        json={"is_active": False},
    )
    assert response.status_code == 200
    assert (await client.get("/api/v1/auth/me")).status_code == 401
    assert (await login(client)).status_code == 401


async def test_login_rejects_untrusted_origin_and_rate_limits_repeated_failures(
    client: httpx.AsyncClient,
    test_database_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Verify login requires an allowed Origin and bounds repeated identity/client failures."""

    await provision_user(test_database_session_factory)
    untrusted_origin = await client.post(
        "/api/v1/auth/login",
        headers={"Origin": "https://attacker.example"},
        json={"email": "user@example.com", "password": TEST_PASSWORD},
    )
    assert untrusted_origin.status_code == 403

    for _ in range(5):
        failed = await login(client, password="this password is definitely wrong")
        assert failed.status_code == 401
    limited = await login(client, password="this password is definitely wrong")
    assert limited.status_code == 429
    assert limited.json() == {"detail": "登录尝试过于频繁，请稍后重试。"}


async def test_session_listing_and_revocation_hide_another_users_session(
    client: httpx.AsyncClient,
    test_database_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Verify session management exposes and mutates only the current user's sessions."""

    await provision_user(test_database_session_factory)
    await provision_user(
        test_database_session_factory,
        user_id="other-user",
        email="other@example.com",
    )
    assert (await login(client)).status_code == 200
    csrf_token = client.cookies.get("nexuspilot_csrf")
    assert csrf_token

    other_transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(
        transport=other_transport,
        base_url="http://test",
        headers={"X-API-Key": "test-api-key-long-enough"},
    ) as other_client:
        other_login = await login(other_client, email="other@example.com")
        assert other_login.status_code == 200
        other_session_id = other_login.json()["login_session_id"]
        sessions = await client.get("/api/v1/auth/sessions")
        assert sessions.status_code == 200
        assert len(sessions.json()) == 1
        assert sessions.json()[0]["is_current"] is True

        cross_user_revoke = await client.delete(
            f"/api/v1/auth/sessions/{other_session_id}",
            headers={"Origin": LOGIN_ORIGIN, "X-CSRF-Token": csrf_token},
        )
        assert cross_user_revoke.status_code == 404
        assert (await other_client.get("/api/v1/auth/me")).status_code == 200


async def test_password_change_keeps_current_session_and_revokes_other_sessions(
    client: httpx.AsyncClient,
    test_database_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Verify a password replacement invalidates other sessions and the old verifier."""

    await provision_user(test_database_session_factory)
    assert (await login(client)).status_code == 200
    other_transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(
        transport=other_transport,
        base_url="http://test",
        headers={"X-API-Key": "test-api-key-long-enough"},
    ) as other_client:
        assert (await login(other_client)).status_code == 200
        csrf_token = client.cookies.get("nexuspilot_csrf")
        assert csrf_token
        changed = await client.post(
            "/api/v1/auth/password/change",
            headers={"Origin": LOGIN_ORIGIN, "X-CSRF-Token": csrf_token},
            json={
                "current_password": TEST_PASSWORD,
                "new_password": "a replacement password with enough length",
            },
        )
        assert changed.status_code == 204
        assert (await client.get("/api/v1/auth/me")).status_code == 200
        assert (await other_client.get("/api/v1/auth/me")).status_code == 401

    old_password = await login(client, password=TEST_PASSWORD)
    assert old_password.status_code == 401
    new_password = await login(
        client,
        password="a replacement password with enough length",
    )
    assert new_password.status_code == 200
