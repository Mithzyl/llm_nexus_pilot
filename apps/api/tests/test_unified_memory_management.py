"""Exercise linked-entry management with real service authorization and encrypted storage."""

import pytest
from test_product_memory import memory_key, native_run  # noqa: F401

from nexuspilot_api.features.identity.models.account_linking import EcosystemAccountLink
from nexuspilot_api.features.identity.models.applications import Application
from nexuspilot_api.features.identity.services.application_identity import resolve_application_user
from nexuspilot_api.models.base import utc_now


@pytest.mark.usefixtures("memory_key")
async def test_native_manages_linked_entry_without_merging_native_memory(
    resource_client,
    test_database_session_factory,
):
    """Linked memory writes retain App ownership; revoking the link removes management access."""
    run = await native_run(resource_client, "manage")
    async with test_database_session_factory() as db:
        db.add(Application(application_id="personal_assistant"))
        await db.commit()
        mapping = await resolve_application_user(
            db, application_id="personal_assistant", external_user_id="one"
        )
        link = EcosystemAccountLink(
            primary_principal_id=run["user_id"],
            linked_principal_id=mapping.pilot_principal_id,
            mapping_id=mapping.mapping_id,
            app_verified_at=utc_now(),
            browser_verified_at=utc_now(),
            verified_at=utc_now(),
        )
        db.add(link)
        await db.commit()
        path = "/api/v1/me/user-memories?entry=personal_assistant"
        response = await resource_client.post(
            path, json={"action": "save", "key": "managed", "text": "App fact"}
        )
        assert response.status_code == 200, response.text
        assert not (await resource_client.get("/api/v1/me/user-memories")).json()["items"]
        assert (await resource_client.get(path)).json()["items"][0]["text"] == "App fact"
        from nexuspilot_api.features.memory.services.automatic_recall import query_automatic
        from nexuspilot_api.features.memory.services.product_memory import MemoryService
        from nexuspilot_api.infrastructure.object_storage import get_object_storage
        from nexuspilot_api.main import app

        source = MemoryService(
            db, app.dependency_overrides[get_object_storage](), run["user_id"], "native"
        )
        assert not (await query_automatic(source))["items"]
        state = (
            await resource_client.get("/api/v1/me/memory-settings?entry=personal_assistant")
        ).json()
        consent = await resource_client.post(
            path,
            json={
                "action": "automatic_sharing",
                "key": "consent",
                "enabled": True,
                "expected_version": state["version"],
            },
        )
        assert consent.status_code == 200, consent.text
        await db.rollback()
        assert (await query_automatic(source))["items"][0]["text"] == "App fact"
        history = await resource_client.get(
            path + "&history_entry_id=" + response.json()["entry_id"]
        )
        assert history.json()["items"][0]["version"] == 1
        link = await db.get(EcosystemAccountLink, link.link_id)
        link.status = "revoked"
        await db.commit()
        assert (await resource_client.get(path)).status_code in {404, 409}
        assert not (await query_automatic(source))["items"]
