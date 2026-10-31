"""Opt-in MySQL row-lock checks; SQLite cannot establish these concurrency guarantees."""

import asyncio
import os

import pytest
from sqlalchemy import func, select
from test_authentication import enable_browser_authentication, login, provision_user  # noqa: F401


@pytest.mark.skipif(
    not os.environ.get("NEXUSPILOT_TEST_AUTH_MYSQL_URL"), reason="Requires real MySQL row locks"
)
@pytest.mark.usefixtures("enable_browser_authentication")
@pytest.mark.parametrize("case", ["same-ticket", "two-tickets", "two-app-accounts"])
async def test_competing_confirmations_create_one_association(
    case, resource_client, test_database_session_factory
):
    """Concurrent consent creates one association and rejects the competing confirmation."""
    from nexuspilot_api.core.errors import InvalidRequestError, ResourceConflictError
    from nexuspilot_api.features.identity.models.account_linking import EcosystemAccountLink
    from nexuspilot_api.features.identity.models.applications import Application
    from nexuspilot_api.features.identity.models.authentication import UserLoginSession
    from nexuspilot_api.features.identity.services.account_linking import begin_link, confirm_link
    from nexuspilot_api.features.identity.services.application_identity import (
        resolve_application_user,
    )

    await provision_user(
        test_database_session_factory, user_id="concurrent-owner", email="concurrent@example.test"
    )
    assert (await login(resource_client, email="concurrent@example.test")).status_code == 200
    async with test_database_session_factory() as db:
        db.add(Application(application_id="personal_assistant"))
        await db.commit()
        first = await resolve_application_user(
            db, application_id="personal_assistant", external_user_id="first"
        )
        session_id = await db.scalar(
            select(UserLoginSession.login_session_id).where(
                UserLoginSession.user_id == "concurrent-owner"
            )
        )
        ticket = await begin_link(db, mapping_id=first.mapping_id)
        tickets = [ticket, ticket]
        if case == "two-tickets":
            tickets[1] = await begin_link(db, mapping_id=first.mapping_id)
        elif case == "two-app-accounts":
            second = await resolve_application_user(
                db, application_id="personal_assistant", external_user_id="second"
            )
            tickets[1] = await begin_link(db, mapping_id=second.mapping_id)
    ready = asyncio.Event()

    async def confirm_once(value):
        """Use an independent transaction and normalize only explicit business rejections."""
        async with test_database_session_factory() as db:
            await ready.wait()
            try:
                await confirm_link(
                    db, ticket=value, owner="concurrent-owner", session_id=session_id
                )
                return "confirmed"
            except (InvalidRequestError, ResourceConflictError):
                await db.rollback()
                return "rejected"

    tasks = [asyncio.create_task(confirm_once(value)) for value in tickets]
    ready.set()
    outcomes = await asyncio.wait_for(asyncio.gather(*tasks), timeout=20)
    assert sorted(outcomes) == ["confirmed", "rejected"]
    async with test_database_session_factory() as db:
        assert await db.scalar(select(func.count()).select_from(EcosystemAccountLink)) == 1
        link = await db.scalar(select(EcosystemAccountLink))
        assert link.primary_principal_id == "concurrent-owner"
        assert link.linked_principal_id != "concurrent-owner"
