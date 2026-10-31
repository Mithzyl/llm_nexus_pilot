"""Verify user-approved field projection before any shared memory reaches a recipient."""

import pytest
from test_product_memory import memory_key, native_run  # noqa: F401

from nexuspilot_api.features.memory.services.product_memory import MemoryFailure, MemoryService
from nexuspilot_api.infrastructure.object_storage import get_object_storage
from nexuspilot_api.main import app


@pytest.mark.usefixtures("memory_key")
async def test_sharing_projects_fields_and_invalidates_on_correction(
    resource_client,
    test_database_session_factory,
):
    """A shared label never includes the source address or summary; edits invalidate approval."""
    from sqlalchemy import select

    from nexuspilot_api.features.identity.models.applications import (
        Application,
        ApplicationUserMapping,
        RunApplicationContext,
    )
    from nexuspilot_api.features.memory.services.sharing import (
        disclose_shared,
        grant_share,
        read_shared,
        shared_query_page,
    )

    run = await native_run(resource_client, "sharing fixture")
    app_run = await native_run(resource_client, "recipient fixture")
    storage = app.dependency_overrides[get_object_storage]()
    async with test_database_session_factory() as db:
        db.add(Application(application_id="personal_assistant"))
        await db.flush()
        db.add(
            ApplicationUserMapping(
                application_id="personal_assistant",
                external_user_id="fixture",
                pilot_principal_id=run["user_id"],
            )
        )
        await db.commit()
        source = MemoryService(db, storage, run["user_id"], "native")
        saved = await source.mutate(
            {
                "action": "save",
                "key": "share-source",
                "text": "Private address summary",
                "typed_data": {
                    "memory_type": "place",
                    "label": "Home",
                    "address": "private address",
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
            automatic=False,
        )
        replay = await resource_client.post(
            "/api/v1/me/memory-shares",
            json={
                "entry_id": saved["entry_id"],
                "expected_version": 1,
                "target_entry": "personal_assistant",
                "fields": ["typed_data.label"],
                "automatic": False,
            },
        )
        assert replay.status_code == 200, replay.text
        assert replay.json()["grant_id"] == grant["grant_id"]
        recipient = MemoryService(db, storage, run["user_id"], "personal_assistant")
        items = await read_shared(recipient, for_model=False)
        assert items[0]["typed_data"] == {"label": "Home"}
        assert "text" not in items[0] and "address" not in str(items[0])
        assert (await read_shared(recipient, for_model=True)) == []
        mapping = await db.scalar(
            select(ApplicationUserMapping).where(
                ApplicationUserMapping.application_id == "personal_assistant"
            )
        )
        db.add(
            RunApplicationContext(
                run_id=app_run["run_id"],
                mapping_id=mapping.mapping_id,
                agent_id="fixture",
                allowed_tool_versions={},
            )
        )
        await db.commit()
        recipient.run_id = app_run["run_id"]
        active = await grant_share(
            source,
            entry_id=saved["entry_id"],
            expected_version=1,
            target_entry="personal_assistant",
            fields=["typed_data.label"],
            automatic=True,
        )
        hidden = await shared_query_page(recipient)
        assert hidden["items"][0]["disclosure_required"]
        await disclose_shared(
            recipient,
            grant_id=active["grant_id"],
            expected_version=active["version"],
            key="explicit-run-share",
        )
        visible = await shared_query_page(recipient)
        assert visible["items"][0]["typed_data"] == {"label": "Home"}
        import json
        from types import SimpleNamespace

        from nexuspilot_models.contracts import Message

        from nexuspilot_api.features.memory.services.memory_tools import (
            memory_checkpoint_message,
            restore_current_memory_messages,
        )

        message = Message(
            role="tool",
            tool_call_id="shared-call",
            tool_name="query_memory",
            content=json.dumps({"result": {"items": [], "shared_items": visible["items"]}}),
        )
        encoded = memory_checkpoint_message(message)
        assert "Home" not in encoded["content"]
        record = SimpleNamespace(
            tool_name="query_memory",
            status="completed",
            provider_tool_call_id="shared-call",
            input_json={},
        )
        restored = Message.model_validate(encoded)
        await restore_current_memory_messages(db, storage, app_run["run_id"], [record], [restored])
        assert json.loads(restored.content)["result"]["shared_items"][0]["typed_data"] == {
            "label": "Home"
        }
        await grant_share(
            source,
            entry_id=saved["entry_id"],
            expected_version=1,
            target_entry="personal_assistant",
            fields=["typed_data.label", "typed_data.address"],
            automatic=True,
        )
        assert (await shared_query_page(recipient))["items"][0]["disclosure_required"]
        await restore_current_memory_messages(db, storage, app_run["run_id"], [record], [restored])
        assert json.loads(restored.content)["result"]["shared_items"] == []
        with pytest.raises(MemoryFailure):
            await grant_share(
                recipient,
                entry_id=saved["entry_id"],
                expected_version=1,
                target_entry="retail",
                fields=["text"],
                automatic=True,
            )
        await source.mutate(
            {
                "action": "correct",
                "key": "share-edit",
                "entry_id": saved["entry_id"],
                "expected_version": 1,
                "text": "Corrected",
                "confirmed_sensitive": True,
            }
        )
        assert await read_shared(recipient, for_model=False) == []
        assert grant["version"] == 1
