"""Cross-account global instructions require an exact current recipient approval."""

import pytest
from test_product_memory import memory_key, native_run  # noqa: F401

from nexuspilot_api.infrastructure.object_storage import get_object_storage
from nexuspilot_api.main import app


@pytest.mark.usefixtures("memory_key")
async def test_linked_global_instructions_pin_text_but_not_revoked_access(
    resource_client, test_database_session_factory
):
    """App-specific instructions remain local; global text needs consent and vanishes on unlink."""
    from nexuspilot_api.features.identity.models.account_linking import EcosystemAccountLink
    from nexuspilot_api.features.identity.models.applications import Application
    from nexuspilot_api.features.identity.services.account_linking import revoke_link
    from nexuspilot_api.features.identity.services.application_identity import (
        resolve_application_user,
    )
    from nexuspilot_api.features.identity.services.user_instructions import (
        pin_run_instructions,
        read_run_instructions,
        save_profile,
    )
    from nexuspilot_api.models import LlmRun
    from nexuspilot_api.models.base import utc_now

    run = await native_run(resource_client, "instruction link fixture")
    storage = app.dependency_overrides[get_object_storage]()
    async with test_database_session_factory() as db:
        db.add(Application(application_id="personal_assistant"))
        await db.commit()
        mapping = await resolve_application_user(
            db, application_id="personal_assistant", external_user_id="instruction-fixture"
        )
        link = EcosystemAccountLink(
            primary_principal_id=run["user_id"],
            linked_principal_id=mapping.pilot_principal_id,
            mapping_id=mapping.mapping_id,
            status="active",
            app_verified_at=utc_now(),
            browser_verified_at=utc_now(),
            verified_at=utc_now(),
        )
        db.add(link)
        await db.commit()
        await save_profile(
            db,
            storage,
            run["user_id"],
            "native",
            "global",
            text="Concise global preference",
            enabled=True,
            allowed_entries=["personal_assistant"],
            expected_version=0,
        )
        await save_profile(
            db,
            storage,
            mapping.pilot_principal_id,
            "personal_assistant",
            "entry",
            text="App only preference",
            enabled=True,
            allowed_entries=[],
            expected_version=0,
        )
        pending = LlmRun(user_id=mapping.pilot_principal_id, user_request="Synthetic App request")
        db.add(pending)
        await db.flush()
        await pin_run_instructions(db, pending, "personal_assistant")
        await db.commit()
        selected = await read_run_instructions(db, storage, pending.run_id)
        assert [r["text"] for r in selected] == ["Concise global preference", "App only preference"]
        await save_profile(
            db,
            storage,
            run["user_id"],
            "native",
            "global",
            text="New preference",
            enabled=True,
            allowed_entries=["personal_assistant"],
            expected_version=1,
        )
        assert (await read_run_instructions(db, storage, pending.run_id))[0][
            "text"
        ] == "Concise global preference"
        await revoke_link(db, link_id=link.link_id, owner=run["user_id"])
        assert [r["text"] for r in await read_run_instructions(db, storage, pending.run_id)] == [
            "App only preference"
        ]
        link.status, link.revoked_at = "active", None
        link.version += 1
        await db.commit()
        # Reconnecting the same accounts is not renewed permission to share old instructions.
        assert [r["text"] for r in await read_run_instructions(db, storage, pending.run_id)] == [
            "App only preference"
        ]
        await save_profile(
            db,
            storage,
            run["user_id"],
            "native",
            "global",
            text="New preference",
            enabled=True,
            allowed_entries=["personal_assistant"],
            expected_version=2,
        )
        assert [r["text"] for r in await read_run_instructions(db, storage, pending.run_id)] == [
            "App only preference"
        ]
        fresh = LlmRun(user_id=mapping.pilot_principal_id, user_request="After renewed consent")
        db.add(fresh)
        await db.flush()
        await pin_run_instructions(db, fresh, "personal_assistant")
        await db.commit()
        assert [r["text"] for r in await read_run_instructions(db, storage, fresh.run_id)] == [
            "New preference",
            "App only preference",
        ]
        await revoke_link(db, link_id=link.link_id, owner=run["user_id"])
        disabled = await save_profile(
            db,
            storage,
            run["user_id"],
            "native",
            "global",
            text="New preference",
            enabled=False,
            allowed_entries=["personal_assistant"],
            expected_version=3,
        )
        assert disabled["enabled"] is False
