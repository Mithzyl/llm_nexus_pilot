"""Check approved scene boundaries and immutable Run selections without model calls."""

import pytest
from test_api import create_test_run


async def test_scene_selection_rejects_other_entry_and_keeps_template_version(
    client, test_database_session_factory
):
    """A Run retains its selected template after the operator changes the default binding."""
    from nexuspilot_api.core.errors import InvalidRequestError
    from nexuspilot_api.models import LlmRun
    from nexuspilot_api.models.prompt_catalog import LlmPromptTemplate, LlmPromptTemplateVersion
    from nexuspilot_api.models.prompt_scenes import PromptSceneBinding
    from nexuspilot_api.schemas.responses import ResponsesRequest
    from nexuspilot_api.services.prompt_scene_service import apply_scene, pin_scene

    created = await create_test_run(client)
    async with test_database_session_factory() as db:
        template = LlmPromptTemplate(template_name="scene-test", current_version_number=1)
        db.add(template)
        await db.flush()
        version = LlmPromptTemplateVersion(
            template_name="scene-test",
            version_number=1,
            content_text="Approved native scene",
            variable_schema_json={},
            created_by_actor_id="fixture",
        )
        db.add(version)
        await db.flush()
        binding = PromptSceneBinding(
            entry="native",
            scene_id="writing",
            execution_mode="quick",
            template_version_id=version.template_version_id,
        )
        db.add(binding)
        await db.commit()
        from test_prompt_catalog import INTERNAL_HEADERS

        registered = await client.post(
            "/api/v1/internal/prompt-scenes",
            headers=INTERNAL_HEADERS,
            json={
                "entry": "native",
                "scene_id": "default",
                "execution_mode": "any",
                "template_version_id": version.template_version_id,
                "expected_version": 0,
            },
        )
        assert registered.status_code == 200, registered.text
        previous = await db.get(LlmRun, created["run_id"])
        default_run = await client.post(
            "/api/v1/runs", json={"user_id": previous.user_id, "user_request": "default scene test"}
        )
        assert default_run.status_code == 201, default_run.text
        default_payload = ResponsesRequest(
            run_id=default_run.json()["run_id"], provider="openai", model="fixed", input="hello"
        )
        assert (await apply_scene(db, default_payload)).instructions == "Approved native scene"
        previous = await db.get(LlmRun, created["run_id"])
        run = LlmRun(
            user_id=previous.user_id,
            session_id=previous.session_id,
            user_request="scene test",
            run_type="general",
        )
        db.add(run)
        await db.flush()
        with pytest.raises(InvalidRequestError):
            await pin_scene(db, run, "personal_assistant", "writing")
        await pin_scene(db, run, "native", "writing")
        await db.commit()
        binding.enabled = False
        await db.commit()
        payload = ResponsesRequest(
            run_id=run.run_id,
            provider="openai",
            model="fixed",
            input="hello",
            instructions="Role instructions",
        )
        prepared = await apply_scene(db, payload)
        assert prepared.instructions == "Approved native scene\n\nRole instructions"
        with pytest.raises(InvalidRequestError):
            await apply_scene(
                db, payload.model_copy(update={"metadata": {"agent_role": "controller"}})
            )
