"""Verify single-use SSO grants with synthetic users and no external application calls."""

import pytest
from sqlalchemy import select
from test_authentication import enable_browser_authentication, login, provision_user  # noqa: F401


@pytest.mark.usefixtures("enable_browser_authentication")
async def test_login_code_is_pkce_bound_and_single_use(
    resource_client, test_database_session_factory, monkeypatch
):
    """Verify single use, owner binding, expiry and revocation without consuming rejected grants."""
    import json

    from pydantic import SecretStr

    from nexuspilot_api.core.config import get_settings
    from nexuspilot_api.core.errors import InvalidRequestError
    from nexuspilot_api.features.identity.models.applications import Application
    from nexuspilot_api.features.identity.models.authentication import UserLoginSession
    from nexuspilot_api.features.identity.services.sso import (
        exchange_code,
        issue_code,
        pkce_challenge,
    )

    monkeypatch.setattr(
        get_settings(),
        "app_tools_json",
        SecretStr(
            json.dumps(
                {
                    "personal_assistant": {
                        "inbound_key": "fixture-inbound-" + "x" * 32,
                        "service_token": "fixture-callback-" + "x" * 32,
                        "base_url": "https://approved.example",
                        "operations": {"personal_assistant_search_notes": "/tools"},
                        "login_redirect_uris": ["http://localhost:3001/callback"],
                    }
                }
            )
        ),
    )

    await provision_user(
        test_database_session_factory, user_id="sso-owner", email="sso@example.test"
    )
    assert (await login(resource_client, email="sso@example.test")).status_code == 200
    async with test_database_session_factory() as db:
        db.add(Application(application_id="personal_assistant"))
        await db.commit()
        session = await db.scalar(
            select(UserLoginSession).where(UserLoginSession.user_id == "sso-owner")
        )
        verifier = "v" * 64
        code = await issue_code(
            db,
            owner="sso-owner",
            session_id=session.login_session_id,
            app_id="personal_assistant",
            redirect_uri="http://localhost:3001/callback",
            challenge=pkce_challenge(verifier),
        )
        with pytest.raises(InvalidRequestError):
            await exchange_code(
                db,
                app_id="personal_assistant",
                code=code,
                redirect_uri="http://localhost:3001/callback",
                verifier="x" * 64,
            )
        result = await exchange_code(
            db,
            app_id="personal_assistant",
            code=code,
            redirect_uri="http://localhost:3001/callback",
            verifier=verifier,
        )
        assert result["subject"] == "sso-owner"
        assert result["external_user_id"] is None
        assert result["registration_ticket"]
        from nexuspilot_api.core.errors import ResourceConflictError
        from nexuspilot_api.features.identity.services.sso import complete_registration

        completed = await complete_registration(
            db,
            app_id="personal_assistant",
            ticket=result["registration_ticket"],
            external_user_id="new-app-user",
        )
        assert completed["subject"] == "sso-owner"
        assert (
            await complete_registration(
                db,
                app_id="personal_assistant",
                ticket=result["registration_ticket"],
                external_user_id="new-app-user",
            )
            == completed
        )
        with pytest.raises(ResourceConflictError):
            await complete_registration(
                db,
                app_id="personal_assistant",
                ticket=result["registration_ticket"],
                external_user_id="different-user",
            )
        await db.rollback()
        with pytest.raises(InvalidRequestError):
            await exchange_code(
                db,
                app_id="personal_assistant",
                code=code,
                redirect_uri="http://localhost:3001/callback",
                verifier=verifier,
            )
    response = await resource_client.post(
        "/api/v1/me/application-login",
        headers={
            "Origin": "http://test",
            "X-CSRF-Token": resource_client.cookies["nexuspilot_csrf"],
        },
        json={
            "application_id": "personal_assistant",
            "redirect_uri": "http://localhost:3001/callback",
            "code_challenge": pkce_challenge(verifier),
            "state": "sso-test-state-001",
        },
    )
    assert response.status_code == 200
    exchanged = await resource_client.post(
        "/api/v1/auth/app/exchange",
        headers={
            "Cookie": "",
            "X-Application-Id": "personal_assistant",
            "Authorization": "Bearer fixture-inbound-" + "x" * 32,
        },
        json={
            "code": response.json()["code"],
            "redirect_uri": "http://localhost:3001/callback",
            "code_verifier": verifier,
        },
    )
    assert exchanged.status_code == 200
    assert exchanged.json()["external_user_id"] == "new-app-user"
    assert exchanged.headers["cache-control"] == "no-store"

    # Subsequent grants must still honor expiry and logout, even for an already mapped user.
    from datetime import timedelta

    from nexuspilot_api.features.identity.models.sso import ApplicationLoginGrant
    from nexuspilot_api.models.base import utc_now

    async with test_database_session_factory() as db:
        session = await db.scalar(
            select(UserLoginSession).where(UserLoginSession.user_id == "sso-owner")
        )
        for invalidation in ("expired", "revoked"):
            code = await issue_code(
                db,
                owner="sso-owner",
                session_id=session.login_session_id,
                app_id="personal_assistant",
                redirect_uri="http://localhost:3001/callback",
                challenge=pkce_challenge(verifier),
            )
            from nexuspilot_api.features.identity.services.authentication_service import (
                authentication_token_hash,
            )

            grant = await db.scalar(
                select(ApplicationLoginGrant).where(
                    ApplicationLoginGrant.code_digest == authentication_token_hash(code)
                )
            )
            if invalidation == "expired":
                grant.expires_at = utc_now() - timedelta(seconds=1)
            else:
                session.revoked_at = utc_now()
            await db.commit()
            with pytest.raises(InvalidRequestError):
                await exchange_code(
                    db,
                    app_id="personal_assistant",
                    code=code,
                    redirect_uri="http://localhost:3001/callback",
                    verifier=verifier,
                )
            assert grant.consumed_at is None
