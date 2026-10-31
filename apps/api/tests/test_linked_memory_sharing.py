"""Verify projected memory access through explicit links without rewriting ciphertext ownership."""

import pytest
from test_product_memory import memory_key, native_run  # noqa: F401

from nexuspilot_api.features.memory.services.product_memory import MemoryFailure, MemoryService
from nexuspilot_api.features.memory.services.sharing import grant_share, read_shared
from nexuspilot_api.infrastructure.object_storage import get_object_storage
from nexuspilot_api.main import app


@pytest.mark.usefixtures("memory_key")
async def test_linked_owners_share_only_selected_fields_and_revoke_both_directions(
    resource_client, test_database_session_factory
):
    """Both App directions use saved links; revoking either link blocks the dependent grant."""
    from nexuspilot_api.features.identity.models.account_linking import EcosystemAccountLink
    from nexuspilot_api.features.identity.models.applications import Application
    from nexuspilot_api.features.identity.services.account_linking import revoke_link
    from nexuspilot_api.features.identity.services.application_identity import (
        resolve_application_user,
    )
    from nexuspilot_api.models.base import utc_now

    run = await native_run(resource_client, "linked sharing fixture")
    storage = app.dependency_overrides[get_object_storage]()
    async with test_database_session_factory() as db:
        links, mappings = {}, {}
        for entry in ("personal_assistant", "retail"):
            db.add(Application(application_id=entry))
            await db.commit()
            mapping = await resolve_application_user(
                db, application_id=entry, external_user_id="fixture"
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
            links[entry], mappings[entry] = link.link_id, mapping
        source = MemoryService(db, storage, run["user_id"], "native")
        recipient = MemoryService(
            db, storage, mappings["personal_assistant"].pilot_principal_id, "personal_assistant"
        )
        retail = MemoryService(db, storage, mappings["retail"].pilot_principal_id, "retail")
        saved = await source.mutate(
            {
                "action": "save",
                "key": "original",
                "text": "Private address",
                "typed_data": {
                    "memory_type": "place",
                    "label": "Home",
                    "address": "not shared",
                    "coordinate_system": "GCJ02",
                },
                "confirmed_type": True,
                "confirmed_sensitive": True,
            }
        )
        grant = await grant_share(
            source,
            entry_id=saved["entry_id"],
            expected_version=1,
            target_entry="personal_assistant",
            fields=["typed_data.label"],
            automatic=True,
        )
        items = await read_shared(recipient, for_model=False)
        assert items[0]["typed_data"] == {"label": "Home"}
        assert "address" not in str(items) and "text" not in items[0]
        mappings["personal_assistant"].is_enabled = False
        await db.commit()
        assert await read_shared(recipient, for_model=False) == []
        mappings["personal_assistant"].is_enabled = True
        await db.commit()
        assert (await read_shared(recipient, for_model=True))[0]["disclosure_required"]
        assert await read_shared(retail, for_model=False) == []
        from nexuspilot_api.features.identity.models.applications import RunApplicationContext
        from nexuspilot_api.features.memory.services.sharing import disclose_shared
        from nexuspilot_api.models import LlmRun

        runs = []
        for _ in range(2):
            pending = LlmRun(user_id=recipient.owner, user_request="synthetic recipient")
            db.add(pending)
            await db.flush()
            db.add(
                RunApplicationContext(
                    run_id=pending.run_id,
                    mapping_id=mappings["personal_assistant"].mapping_id,
                    agent_id="fixture",
                    allowed_tool_versions={},
                )
            )
            await db.commit()
            runs.append(pending.run_id)
        recipient.run_id = runs[0]
        await disclose_shared(
            recipient, grant_id=grant["grant_id"], expected_version=grant["version"], key="run-one"
        )
        assert (await read_shared(recipient, for_model=True))[0]["typed_data"] == {"label": "Home"}
        recipient.run_id = runs[1]
        assert (await read_shared(recipient, for_model=True))[0]["disclosure_required"]
        await disclose_shared(
            recipient, grant_id=grant["grant_id"], expected_version=grant["version"], key="run-two"
        )
        import json
        from types import SimpleNamespace

        from nexuspilot_models.contracts import Message

        from nexuspilot_api.features.memory.services.memory_tools import (
            memory_checkpoint_message,
            restore_current_memory_messages,
        )

        visible = await read_shared(recipient, for_model=True)
        message = Message(
            role="tool",
            tool_call_id="linked-read",
            tool_name="query_memory",
            content=json.dumps({"result": {"items": [], "shared_items": visible}}),
        )
        checkpoint = memory_checkpoint_message(message)
        assert "Home" not in checkpoint["content"]
        restored = Message.model_validate(checkpoint)
        tool = SimpleNamespace(
            tool_name="query_memory",
            status="completed",
            provider_tool_call_id="linked-read",
            input_json={},
        )
        await restore_current_memory_messages(db, storage, runs[1], [tool], [restored])
        assert json.loads(restored.content)["result"]["shared_items"][0]["typed_data"] == {
            "label": "Home"
        }
        reverse = await recipient.mutate(
            {"action": "save", "key": "app-original", "text": "Prefer short answers"}
        )
        await grant_share(
            recipient,
            entry_id=reverse["entry_id"],
            expected_version=1,
            target_entry="native",
            fields=["text"],
            automatic=True,
        )
        assert (await read_shared(source, for_model=False))[0]["text"] == "Prefer short answers"
        await grant_share(
            recipient,
            entry_id=reverse["entry_id"],
            expected_version=1,
            target_entry="retail",
            fields=["text"],
            automatic=True,
        )
        assert (await read_shared(retail, for_model=False))[0]["text"] == "Prefer short answers"
        await revoke_link(db, link_id=links["retail"], owner=run["user_id"])
        assert await read_shared(retail, for_model=False) == []
        assert (await read_shared(source, for_model=False))[0]["text"] == "Prefer short answers"
        await revoke_link(db, link_id=links["personal_assistant"], owner=run["user_id"])
        assert await read_shared(recipient, for_model=False) == []
        assert await read_shared(source, for_model=False) == []
        assert await read_shared(retail, for_model=False) == []
        await restore_current_memory_messages(db, storage, runs[1], [tool], [restored])
        assert json.loads(restored.content)["result"]["shared_items"] == []
        with pytest.raises(MemoryFailure):
            await grant_share(
                source,
                entry_id=saved["entry_id"],
                expected_version=1,
                target_entry="personal_assistant",
                fields=["typed_data.label"],
                automatic=True,
            )
