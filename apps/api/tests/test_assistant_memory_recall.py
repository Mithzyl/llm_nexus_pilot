"""Check Assistant-only automatic recall without widening native or Retail disclosure."""

import pytest
from test_api import create_test_run
from test_product_memory import memory_key  # noqa: F401

from nexuspilot_api.features.memory.services.product_memory import MemoryService
from nexuspilot_api.infrastructure.object_storage import get_object_storage
from nexuspilot_api.main import app

pytestmark = pytest.mark.usefixtures("memory_key")


@pytest.mark.parametrize("entry", ["personal_assistant", "native", "retail"])
async def test_own_assistant_recall_does_not_require_run_disclosure(
    client,
    test_database_session_factory,
    entry,
):
    """Assistant recalls own active facts; other entries retain disclosure requirements."""
    run = await create_test_run(client)
    async with test_database_session_factory() as db:
        service = MemoryService(
            db, app.dependency_overrides[get_object_storage](), run["user_id"], entry
        )
        await service.mutate(
            {"action": "save", "key": "home", "text": "家庭地址测试", "confirmed_sensitive": True}
        )
        result = (await service.query(automatic=True, for_model=True, query="地址"))["items"][0]
        assert ("text" in result) == (entry == "personal_assistant")
        state = await service.settings()
        await service.mutate(
            {
                "action": "settings",
                "key": "off",
                "enabled": False,
                "expected_version": state["version"],
            }
        )
        assert not (await service.query(automatic=True, for_model=True))["items"]
