"""Verify explicit two-party association without moving existing resource ownership."""

import pytest
from sqlalchemy import select
from test_authentication import enable_browser_authentication, login, provision_user  # noqa: F401


@pytest.mark.usefixtures("enable_browser_authentication")
async def test_confirm_and_revoke_preserve_app_owner(
    resource_client, test_database_session_factory, monkeypatch
):
    """Require browser confirmation after App consent, preserving owners on revoke."""
    import json

    from pydantic import SecretStr

    from nexuspilot_api.core.config import get_settings
    from nexuspilot_api.core.errors import InvalidRequestError
    from nexuspilot_api.features.identity.models.applications import Application
    from nexuspilot_api.features.identity.models.authentication import UserLoginSession
    from nexuspilot_api.features.identity.services.account_linking import (
        begin_link,
        confirm_link,
        linked_mapping,
        revoke_link,
    )
    from nexuspilot_api.features.identity.services.application_identity import (
        resolve_application_user,
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
        test_database_session_factory, user_id="nexus-link-user", email="link@example.test"
    )
    assert (await login(resource_client, email="link@example.test")).status_code == 200
    async with test_database_session_factory() as db:
        db.add(Application(application_id="personal_assistant"))
        await db.commit()
        mapping = await resolve_application_user(
            db, application_id="personal_assistant", external_user_id="old-app-user"
        )
        original_owner = mapping.pilot_principal_id
        session = await db.scalar(
            select(UserLoginSession).where(UserLoginSession.user_id == "nexus-link-user")
        )
        ticket = await begin_link(db, mapping_id=mapping.mapping_id)
        assert (
            await linked_mapping(db, owner="nexus-link-user", app_id="personal_assistant") is None
        )
        link = await confirm_link(
            db, ticket=ticket, owner="nexus-link-user", session_id=session.login_session_id
        )
        link_id = link.link_id
        assert mapping.pilot_principal_id == original_owner
        assert (
            await linked_mapping(db, owner="nexus-link-user", app_id="personal_assistant")
        ).mapping_id == mapping.mapping_id
        from nexuspilot_api.features.identity.services.sso import (
            exchange_code,
            issue_code,
            pkce_challenge,
        )

        code = await issue_code(
            db,
            owner="nexus-link-user",
            session_id=session.login_session_id,
            app_id="personal_assistant",
            redirect_uri="http://localhost:3001/callback",
            challenge=pkce_challenge("v" * 64),
        )
        result = await exchange_code(
            db,
            app_id="personal_assistant",
            code=code,
            redirect_uri="http://localhost:3001/callback",
            verifier="v" * 64,
        )
        assert result["external_user_id"] == "old-app-user"
        assert result["registration_ticket"] is None
        with pytest.raises(InvalidRequestError):
            await confirm_link(
                db, ticket=ticket, owner="nexus-link-user", session_id=session.login_session_id
            )
        await db.rollback()
        with pytest.raises(InvalidRequestError):
            await revoke_link(db, link_id=link_id, owner="nexus-link-user", mapping_id="other-map")
        await db.rollback()
        await revoke_link(db, link_id=link_id, owner="nexus-link-user")
        assert (
            await linked_mapping(db, owner="nexus-link-user", app_id="personal_assistant") is None
        )
    app_headers = {
        "Cookie": "",
        "X-Application-Id": "personal_assistant",
        "X-External-User-Id": "old-app-user",
        "Authorization": "Bearer fixture-inbound-" + "x" * 32,
    }
    started = await resource_client.post(
        "/api/v1/app/me/account-link-requests", headers=app_headers
    )
    assert started.status_code == 200
    assert started.headers["cache-control"] == "no-store"
    ticket = started.json()["ticket"]
    browser_headers = {
        "Origin": "http://test",
        "X-CSRF-Token": resource_client.cookies["nexuspilot_csrf"],
    }
    preview = await resource_client.post(
        "/api/v1/me/account-links/preview", headers=browser_headers, json={"ticket": ticket}
    )
    assert preview.status_code == 200
    assert preview.json()["external_user_id"] == "old-app-user"
    confirmed = await resource_client.post(
        "/api/v1/me/account-links/confirm", headers=browser_headers, json={"ticket": ticket}
    )
    assert confirmed.status_code == 200
    foreign = await resource_client.post(
        "/api/v1/app/me/account-links/revoke",
        headers={**app_headers, "X-External-User-Id": "unrelated-user"},
        json={"link_id": link_id},
    )
    assert foreign.status_code == 422
    revoked = await resource_client.post(
        "/api/v1/app/me/account-links/revoke", headers=app_headers, json={"link_id": link_id}
    )
    assert revoked.status_code == 200 and revoked.json()["status"] == "revoked"
    from datetime import timedelta

    from nexuspilot_api.features.identity.models.account_linking import AccountLinkRequest
    from nexuspilot_api.models.base import utc_now

    expired = await resource_client.post(
        "/api/v1/app/me/account-link-requests", headers=app_headers
    )
    assert expired.status_code == 200
    async with test_database_session_factory() as db:
        row = await db.scalar(
            select(AccountLinkRequest).where(AccountLinkRequest.consumed_at.is_(None))
        )
        row.expires_at = utc_now() - timedelta(seconds=1)
        await db.commit()
    denied = await resource_client.post(
        "/api/v1/me/account-links/confirm",
        headers=browser_headers,
        json={"ticket": expired.json()["ticket"]},
    )
    assert denied.status_code == 422
