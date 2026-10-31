"""Email reset changes only an existing verified account and revokes its browser sessions."""

import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr
from test_authentication import enable_browser_authentication, login, provision_user  # noqa: F401


@pytest.mark.usefixtures("enable_browser_authentication")
async def test_reset_without_registration_consumes_code_and_revokes_sessions(
    resource_client, test_database_session_factory, monkeypatch
):
    """Unknown accounts receive the same response; successful reset cannot be replayed."""
    from nexuspilot_api.core.config import get_settings
    from nexuspilot_api.features.identity.services import password_reset

    settings = get_settings()
    monkeypatch.setattr(settings, "password_reset_enabled", True)
    monkeypatch.setattr(settings, "registration_enabled", False)
    monkeypatch.setattr(
        settings, "registration_verification_key", SecretStr(Fernet.generate_key().decode())
    )
    mail = []

    async def deliver(email, code, settings, **kwargs):
        """Capture only synthetic delivery in memory; never send external email."""
        mail.append((email, code))

    monkeypatch.setattr(password_reset, "send_verification_email", deliver)
    await provision_user(
        test_database_session_factory, user_id="reset-user", email="reset@example.test"
    )
    assert (await login(resource_client, email="reset@example.test")).status_code == 200
    headers = {"Origin": "http://test"}
    unknown = await resource_client.post(
        "/api/v1/auth/password-reset/request",
        headers=headers,
        json={"email": "unknown@example.test"},
    )
    known = await resource_client.post(
        "/api/v1/auth/password-reset/request", headers=headers, json={"email": "reset@example.test"}
    )
    assert known.status_code == unknown.status_code == 200
    assert known.json() == unknown.json()
    assert len(mail) == 1
    payload = {
        "email": "reset@example.test",
        "code": mail[0][1],
        "new_password": "New-fixture-password-2026!",
    }
    wrong = await resource_client.post(
        "/api/v1/auth/password-reset/verify",
        headers=headers,
        json={**payload, "code": "incorrect-fixture-code"},
    )
    assert wrong.status_code == 422
    denied = await resource_client.post(
        "/api/v1/auth/password-reset/verify",
        headers={"Origin": "https://unapproved.example"},
        json=payload,
    )
    assert denied.status_code == 403
    reset = await resource_client.post(
        "/api/v1/auth/password-reset/verify", headers=headers, json=payload
    )
    assert reset.status_code == 200
    assert (await resource_client.get("/api/v1/auth/me")).status_code == 401
    replay = await resource_client.post(
        "/api/v1/auth/password-reset/verify", headers=headers, json=payload
    )
    assert replay.status_code == 422
    resource_client.cookies.clear()
    assert (await login(resource_client, email="reset@example.test")).status_code == 401
    logged = await resource_client.post(
        "/api/v1/auth/login",
        headers=headers,
        json={"email": "reset@example.test", "password": payload["new_password"]},
    )
    assert logged.status_code == 200


@pytest.mark.usefixtures("enable_browser_authentication")
async def test_reset_proof_is_invalid_after_credential_changes_at_same_timestamp(
    resource_client, test_database_session_factory, monkeypatch
):
    """MySQL timestamp precision must not let an old proof survive a newer credential."""
    from sqlalchemy import select

    from nexuspilot_api.core.config import get_settings
    from nexuspilot_api.features.identity.models.authentication import UserPasswordCredential
    from nexuspilot_api.features.identity.services import password_reset
    from nexuspilot_api.features.identity.services.password_hasher import hash_password

    settings = get_settings()
    monkeypatch.setattr(settings, "password_reset_enabled", True)
    monkeypatch.setattr(
        settings, "registration_verification_key", SecretStr(Fernet.generate_key().decode())
    )
    codes = []

    async def deliver(email, code, settings, **kwargs):
        """Capture the fixture proof without delivering any external email."""
        codes.append(code)

    monkeypatch.setattr(password_reset, "send_verification_email", deliver)
    await provision_user(
        test_database_session_factory, user_id="changed-reset", email="changed@example.test"
    )
    headers = {"Origin": "http://test"}
    assert (
        await resource_client.post(
            "/api/v1/auth/password-reset/request",
            headers=headers,
            json={"email": "changed@example.test"},
        )
    ).status_code == 200
    async with test_database_session_factory() as db:
        credential = await db.scalar(select(UserPasswordCredential))
        credential.password_hash = hash_password("Independently-changed-2026!")
        await db.commit()
    reset = await resource_client.post(
        "/api/v1/auth/password-reset/verify",
        headers=headers,
        json={
            "email": "changed@example.test",
            "code": codes[0],
            "new_password": "Should-not-be-installed-2026!",
        },
    )
    assert reset.status_code == 422
