"""Exercise App Run creation through the real Agent request builder with a fixed provider."""

import pytest
from test_agent_workflows import register_default_agent_test_model, scripted_registry  # noqa: F401
from test_application_tool_loop import (  # noqa: F401
    app_headers,
    configured_application,
    remote_server,
)
from test_general_agent import GeneralProvider, general_model_catalog, general_payload  # noqa: F401
from test_product_memory import memory_key  # noqa: F401

from nexuspilot_api.core.dependencies import get_provider_registry
from nexuspilot_api.infrastructure.object_storage import get_object_storage
from nexuspilot_api.main import app


@pytest.mark.usefixtures("memory_key")
async def test_app_run_injects_pinned_scene_and_linked_preferences(
    resource_client, configured_application, test_database_session_factory  # noqa: F811
):
    """App endpoints retain scene and preference versions through Controller and Reviewer."""
    from sqlalchemy import select

    from nexuspilot_api.features.identity.models.account_linking import EcosystemAccountLink
    from nexuspilot_api.features.identity.models.applications import ApplicationUserMapping
    from nexuspilot_api.features.identity.services.user_instructions import save_profile
    from nexuspilot_api.models import User
    from nexuspilot_api.models.base import utc_now
    from nexuspilot_api.models.prompt_catalog import LlmPromptTemplate, LlmPromptTemplateVersion
    from nexuspilot_api.models.prompt_scenes import PromptSceneBinding

    headers = app_headers("personal_assistant", "prompt-fixture")
    created = await resource_client.post(
        "/api/v1/app/runs", headers=headers, json={"user_request": "Before consent"}
    )
    assert created.status_code == 201
    app_owner = created.json()["user_id"]
    storage = app.dependency_overrides[get_object_storage]()
    async with test_database_session_factory() as db:
        db.add(User(user_id="prompt-native-owner", display_name="Synthetic", is_active=True))
        await db.commit()
        mapping = await db.scalar(
            select(ApplicationUserMapping).where(
                ApplicationUserMapping.pilot_principal_id == app_owner
            )
        )
        db.add(
            EcosystemAccountLink(
                primary_principal_id="prompt-native-owner",
                linked_principal_id=app_owner,
                mapping_id=mapping.mapping_id,
                status="active",
                app_verified_at=utc_now(),
                browser_verified_at=utc_now(),
                verified_at=utc_now(),
            )
        )
        await db.commit()
        await save_profile(
            db,
            storage,
            "prompt-native-owner",
            "native",
            "global",
            text="GLOBAL-PINNED-FIXTURE",
            enabled=True,
            allowed_entries=["personal_assistant"],
            expected_version=0,
        )
        await save_profile(
            db,
            storage,
            app_owner,
            "personal_assistant",
            "entry",
            text="APP-ONLY-FIXTURE",
            enabled=True,
            allowed_entries=[],
            expected_version=0,
        )
        db.add(LlmPromptTemplate(template_name="app-scene-fixture", current_version_number=1))
        await db.flush()
        version = LlmPromptTemplateVersion(
            template_name="app-scene-fixture",
            version_number=1,
            content_text="SCENE-PINNED-FIXTURE",
            variable_schema_json={},
            created_by_actor_id="fixture",
        )
        db.add(version)
        await db.flush()
        binding = PromptSceneBinding(
            entry="personal_assistant",
            scene_id="default",
            execution_mode="agent",
            template_version_id=version.template_version_id,
        )
        db.add(binding)
        await db.commit()
    created = await resource_client.post(
        "/api/v1/app/runs", headers=headers, json={"user_request": "Explain a synthetic topic"}
    )
    assert created.status_code == 201
    run_id = created.json()["run_id"]
    async with test_database_session_factory() as db:
        await save_profile(
            db,
            storage,
            "prompt-native-owner",
            "native",
            "global",
            text="LATER-EDIT-FIXTURE",
            enabled=True,
            allowed_entries=["personal_assistant"],
            expected_version=1,
        )
    provider = GeneralProvider()
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)
    response = await resource_client.post(
        f"/api/v1/app/runs/{run_id}/agent-workflows",
        headers=headers,
        json=general_payload("always"),
    )
    assert response.status_code == 201, response.text
    assert response.json()["status"] == "completed", response.text
    assert response.json()["final_output"]["final_text"]
    assert {r.metadata["agent_role"] for r in provider.requests} >= {"controller", "reviewer"}
    for request in provider.requests:
        system = request.system_instruction
        assert (
            system.index("SCENE-PINNED-FIXTURE")
            < system.index("GLOBAL-PINNED-FIXTURE")
            < system.index("APP-ONLY-FIXTURE")
        )
        assert "LATER-EDIT-FIXTURE" not in system
    assert configured_application == []
    foreign = await resource_client.get(
        f"/api/v1/app/runs/{run_id}/agent-workflow",
        headers=app_headers("personal_assistant", "another-user"),
    )
    assert foreign.status_code == 404
