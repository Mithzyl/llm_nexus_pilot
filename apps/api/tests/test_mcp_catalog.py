"""Independent MCP aliases remain owner-scoped and retain immutable approval metadata."""

import json

import pytest
from pydantic import SecretStr


async def test_catalog_alias_is_stable_and_rejects_old_permission(
    test_database_session_factory, monkeypatch
):
    """Random persisted aliases need no content hash; old aliases cannot bypass revocation."""
    from nexuspilot_api.core.config import get_settings
    from nexuspilot_api.models import User
    from nexuspilot_api.services.agent_file_tool_service import ToolOperationError
    from nexuspilot_api.services.mcp_catalog import catalog_entries, resolve_binding
    from nexuspilot_api.services.mcp_management import effective_connections, save_connection

    monkeypatch.setattr(
        get_settings(),
        "mcp_connections_json",
        SecretStr(
            json.dumps(
                {
                    "approved": {
                        "url": "https://approved.example/mcp",
                        "token": "fixture-token",
                        "user_ids": ["catalog-owner"],
                        "tools": {
                            "lookup": {
                                "description": "lookup fixture",
                                "input_schema": {
                                    "type": "object",
                                    "properties": {},
                                    "additionalProperties": False,
                                },
                            }
                        },
                    }
                }
            )
        ),
    )
    async with test_database_session_factory() as db:
        db.add(User(user_id="catalog-owner", display_name="Fixture", is_active=True))
        await db.commit()
        configs = await effective_connections(db, "catalog-owner")
        first = await catalog_entries(db, "catalog-owner", configs)
        second = await catalog_entries(db, "catalog-owner", configs)
        assert first[0].name == second[0].name
        assert first[0].name.startswith("mcp_remote_")
        assert first[0].definition().input_schema["additionalProperties"] is False
        binding = await resolve_binding(db, "catalog-owner", first[0].name, configs)
        assert binding.remote_tool_name == "lookup"
        with pytest.raises(ToolOperationError):
            await resolve_binding(db, "other-owner", first[0].name, configs)
        await save_connection(
            db,
            "catalog-owner",
            connection="approved",
            config_version="1.0.0",
            expected_revision=0,
            enabled=False,
            tools=["lookup"],
        )
        with pytest.raises(ToolOperationError):
            await resolve_binding(
                db, "catalog-owner", first[0].name, await effective_connections(db, "catalog-owner")
            )
