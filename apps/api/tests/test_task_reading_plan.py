"""Exercise immutable reading plans through the actual general Agent tool executor."""

import json

import pytest
from nexuspilot_models.contracts import ModelResponse, ToolCall
from sqlalchemy import text
from test_agent_workflows import register_default_agent_test_model, scripted_registry  # noqa: F401
from test_conversation_file_context import prepare_conversation_file
from test_general_agent import GeneralProvider, general_model_catalog, general_payload  # noqa: F401

from nexuspilot_api.core.dependencies import get_provider_registry
from nexuspilot_api.core.errors import ResourceConflictError, ResourceNotFoundError
from nexuspilot_api.features.agent_runtime.services.task_reading_plan import (
    SaveReadingPlanArguments,
    build_reading_plan,
    load_reading_plan,
)
from nexuspilot_api.infrastructure.object_storage import get_object_storage
from nexuspilot_api.main import app
from nexuspilot_api.models import LlmModelAttempt, LlmModelToolCall, UserFile, new_id


@pytest.mark.parametrize("repeat_without_read", [False, True])
async def test_reading_plan_is_saved_and_progress_changes_only_after_read(
    client, test_database_session_factory, repeat_without_read,
):
    """Persist a target scope without reading it; then observe committed file delivery."""
    _, _, file, turn = await prepare_conversation_file(client)

    class Provider(GeneralProvider):
        """Discover plan tools and reuse the saved immutable plan ID."""

        def __init__(self):
            """Retain only fixture observations, not fabricated platform identifiers."""
            super().__init__()
            self.plan_id = None
            self.observations = []

        async def generate(self, request):
            """Save, inspect, read and inspect the exact same plan without replacing it."""
            self.requests.append(request)
            previous = [m for m in request.messages if m.role.value == "tool"]
            if previous:
                last = json.loads(previous[-1].content)
                assert last["status"] == "completed", last
                if previous[-1].tool_name == "save_task_reading_plan":
                    self.plan_id = last["result"]["plan_id"]
                if previous[-1].tool_name == "read_task_reading_plan":
                    self.observations.append(last["result"])
            steps = [
                ("describe_tools", {"name": "save_task_reading_plan", "version": "1.0.0"}),
                ("describe_tools", {"name": "read_task_reading_plan", "version": "1.0.0"}),
                ("save_task_reading_plan", {"targets": [{"file_id": file["file_id"]}]}),
                ("read_task_reading_plan", {"plan_id": self.plan_id}),
                ("read_file_content", {"file_id": file["file_id"], "limit": 1}),
                ("read_task_reading_plan", {"plan_id": self.plan_id}),
            ]
            if repeat_without_read:
                steps[4] = steps[3]
            if len(previous) == len(steps):
                return ModelResponse(text="Source received. [S1]", finish_reason="stop",
                                     latency_ms=1, raw_response={})
            name, arguments = steps[len(previous)]
            return ModelResponse(tool_calls=[ToolCall(id=f"p-{len(previous)}", name=name,
                arguments=arguments)], finish_reason="tool_calls", latency_ms=1, raw_response={})

    provider = Provider()
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)
    response = await client.post(f"/api/v1/runs/{turn['run']['run_id']}/agent-workflows",
                                 json=general_payload("never"))
    assert response.status_code == 201
    if repeat_without_read:
        assert response.json()["status"] == "failed"
        assert "without progress" in response.json()["error"]["public_message"]
        assert len(provider.observations) == 1
        return
    assert response.json()["status"] == "completed", json.dumps(response.json().get("error"))
    before, after = provider.observations
    assert before["plan_id"] == after["plan_id"] == provider.plan_id
    assert before["all_target_blocks_returned"] is False
    assert after["all_target_blocks_returned"] is True
    assert after["model_understanding_verified"] is False
    assert before["targets"] == after["targets"]
    storage = app.dependency_overrides[get_object_storage]()
    async with test_database_session_factory() as db:
        saved = await db.get(LlmModelToolCall, provider.plan_id)
        attempt = await db.get(LlmModelAttempt, saved.attempt_id)
        restored = await load_reading_plan(db, storage, run_id=attempt.run_id,
            task_id=attempt.task_id, plan_id=provider.plan_id)
        assert restored["targets"] == before["targets"]
        raw = (await db.execute(text(
            "SELECT output_json, body_ref_json FROM llm_tool_calls WHERE tool_call_id=:id"
        ), {"id": provider.plan_id})).one()
        assert (json.loads(raw[0]) if isinstance(raw[0], str) else raw[0]) in (None, {})
        assert raw[1]
        with pytest.raises(ResourceNotFoundError):
            await load_reading_plan(db, storage, run_id=attempt.run_id,
                task_id=new_id(), plan_id=provider.plan_id)
        for targets in ([{"file_id": file["file_id"], "block_count": 2}],
                        [{"file_id": file["file_id"]}, {"file_id": file["file_id"]}]):
            with pytest.raises(ResourceConflictError):
                await build_reading_plan(db, storage, run_id=attempt.run_id,
                    plan_id=new_id(), arguments=SaveReadingPlanArguments(targets=targets))
        row = await db.get(UserFile, file["file_id"])
        row.status = "deleted"
        with pytest.raises(ResourceNotFoundError):
            await load_reading_plan(db, storage, run_id=attempt.run_id,
                task_id=attempt.task_id, plan_id=provider.plan_id)


def test_planned_range_counts_only_selected_returned_blocks():
    """Reading elsewhere does not satisfy a declared range or imply whole-document completion."""
    from test_task_read_coverage import coverage_fixture

    from nexuspilot_api.features.agent_runtime.services.task_read_coverage import (
        summarize_task_reads,
    )

    document, file, result = coverage_fixture()
    for bounds, expected in [((0, 1), 0), ((1, 2), 1), ((0, 3), 1)]:
        report = summarize_task_reads(document, file, [result], offset=0, limit=10,
                                     planned_range=bounds)
        assert report["planned_range"]["returned_block_count"] == expected
        assert report["all_parsed_blocks_returned"] is False
        assert report["model_understanding_verified"] is False
