"""Check preference versions and entry selection without invoking any provider."""

import pytest
from test_product_memory import memory_key, native_run  # noqa: F401

from nexuspilot_api.infrastructure.object_storage import get_object_storage
from nexuspilot_api.main import app


@pytest.mark.usefixtures("memory_key")
async def test_instruction_selection_pins_versions_and_explicit_absence(
    resource_client, test_database_session_factory
):
    """Later edits neither change old Runs nor introduce preferences into a previously empty Run."""
    from nexuspilot_api.features.identity.services.user_instructions import (
        read_run_instructions,
        save_profile,
    )

    first = await native_run(resource_client, "before preferences")
    storage = app.dependency_overrides[get_object_storage]()
    async with test_database_session_factory() as db:
        result = await save_profile(
            db,
            storage,
            first["user_id"],
            "native",
            "entry",
            text="Use concise Chinese",
            enabled=True,
            allowed_entries=[],
            expected_version=0,
        )
        assert result["version"] == 1
    second = await native_run(resource_client, "after preferences")
    async with test_database_session_factory() as db:
        await save_profile(
            db,
            storage,
            first["user_id"],
            "native",
            "entry",
            text="Use detailed English",
            enabled=True,
            allowed_entries=[],
            expected_version=1,
        )
        assert await read_run_instructions(db, storage, first["run_id"]) == []
        selected = await read_run_instructions(db, storage, second["run_id"])
        assert selected[0]["text"] == "Use concise Chinese"
        assert selected[0]["version"] == 1
        from nexuspilot_models.contracts import ModelRequest

        from nexuspilot_api.features.identity.services.user_instructions import (
            apply_run_instructions,
        )

        request = ModelRequest(
            provider="openai",
            model="fixed",
            system_instruction="Platform rules",
            messages=[{"role": "user", "content": "hello"}],
        )
        prepared = await apply_run_instructions(db, storage, second["run_id"], request)
        assert prepared.system_instruction.startswith("Platform rules")
        assert "Use concise Chinese" in prepared.system_instruction
        assert "Use detailed English" not in prepared.system_instruction
        assert prepared.tools == []
        ModelRequest.model_validate(prepared.model_dump())
    response = await resource_client.get("/api/v1/me/instructions")
    assert response.status_code == 200
    assert response.json()["version"] == 2
    assert response.json()["text"] == "Use detailed English"
