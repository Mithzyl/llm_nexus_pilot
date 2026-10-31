"""Verify encrypted version retention and conditional undo without model calls."""

import pytest
from test_api import create_test_run
from test_product_memory import memory_key  # noqa: F401

from nexuspilot_api.features.memory.services.product_memory import MemoryFailure, MemoryService
from nexuspilot_api.infrastructure.object_storage import get_object_storage
from nexuspilot_api.main import app

pytestmark = pytest.mark.usefixtures("memory_key")


async def test_memory_undo_creates_version_and_rejects_stale_target(
    client,
    test_database_session_factory,
):
    """Undo restores the preceding content without decreasing versions or overwriting later work."""
    run = await create_test_run(client)
    async with test_database_session_factory() as db:
        service = MemoryService(
            db, app.dependency_overrides[get_object_storage](), run["user_id"], "personal_assistant"
        )
        first = await service.mutate({"action": "save", "key": "first", "text": "Original"})
        second = await service.mutate(
            {
                "action": "correct",
                "key": "second",
                "text": "Changed",
                "entry_id": first["entry_id"],
                "expected_version": 1,
            }
        )
        request = {
            "action": "undo",
            "key": "undo-second",
            "entry_id": first["entry_id"],
            "expected_version": second["version"],
        }
        restored = await service.mutate(request)
        assert restored["version"] == 3
        replayed = await service.mutate({"action": "save", "key": "first", "text": "Original"})
        assert replayed["version"] == 3 and replayed["applied_version"] == 1
        assert (await service.query())["items"][0]["text"] == "Original"
        assert await service.mutate(request) == restored
        with pytest.raises(MemoryFailure, match="memory_version_conflict"):
            await service.mutate({**request, "key": "stale"})
        await service.mutate(
            {
                "action": "forget",
                "key": "forget",
                "entry_id": first["entry_id"],
                "expected_version": 3,
            }
        )
        await service.mutate(
            {
                "action": "undo",
                "key": "undo-forget",
                "entry_id": first["entry_id"],
                "expected_version": 4,
            }
        )
        assert (await service.query())["items"][0]["text"] == "Original"


async def test_automatic_assistant_write_keeps_versions_and_obeys_disabled_scope(
    client,
    test_database_session_factory,
):
    """Trusted automatic writes need no consent flags but cannot bypass disabled memory."""
    run = await create_test_run(client)
    async with test_database_session_factory() as db:
        service = MemoryService(
            db,
            app.dependency_overrides[get_object_storage](),
            run["user_id"],
            "personal_assistant",
            assistant_automatic=True,
        )
        saved = await service.mutate({"action": "save", "key": "auto", "text": "家庭地址测试"})
        assert saved["status"] == "active"
        state = await service.settings()
        await service.mutate(
            {
                "action": "settings",
                "key": "off",
                "enabled": False,
                "expected_version": state["version"],
            }
        )
        with pytest.raises(MemoryFailure, match="memory_disabled"):
            await service.mutate(
                {
                    "action": "correct",
                    "key": "after-off",
                    "text": "改地址",
                    "entry_id": saved["entry_id"],
                    "expected_version": 1,
                }
            )
