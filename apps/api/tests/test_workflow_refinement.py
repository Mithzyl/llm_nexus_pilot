"""Verify bounded Controller refinement and durable repair accounting without paid calls."""

import json

import pytest
from sqlalchemy import select
from test_agent_workflows import (
    ScriptedAgentProvider,
    register_default_agent_test_model,  # noqa: F401
    scripted_registry,
    workflow_payload,
)
from test_api import create_test_run

from nexuspilot_api.core.dependencies import get_provider_registry
from nexuspilot_api.main import app
from nexuspilot_api.models import LlmModelAttempt


class RefinementProvider(ScriptedAgentProvider):
    """Ask the Controller to refine once or persistently, preserving normal node schemas."""

    def __init__(self, *, persistent=False, repair_controller=False):
        """Track independent reviews and optional first-plan format failure."""
        super().__init__()
        self.reviews = 0
        self.plans = 0
        self.persistent = persistent
        self.repair_controller = repair_controller

    def _node_output(self, node_key, request):
        """Generate a real return-to-Controller trigger, not a second tool call."""
        output = super()._node_output(node_key, request)
        if node_key == "controller_planning":
            self.plans += 1
            if request.metadata.get("workflow_round") == "2":
                assert "refinement" in str(request.messages)
                assert "Check omitted evidence" in str(request.messages)
                output["tasks"][0]["objective"] = "Check omitted evidence and refine the analysis"
            if self.repair_controller and self.plans == 1:
                output.pop("decision_summary")
        if node_key == "independent_review":
            self.reviews += 1
            if self.reviews == 1 or self.persistent:
                output.update(
                    verdict="needs_revision",
                    requires_replan=True,
                    review_summary="Check omitted evidence",
                )
        return output


@pytest.mark.parametrize("persistent", [False, True])
async def test_review_returns_to_controller_once_and_preserves_both_rounds(
    client,
    test_database_session_factory,
    persistent,
):
    """Retain first-round facts, cap refinement at two rounds and save exactly one answer."""
    run = await create_test_run(client)
    provider = RefinementProvider(persistent=persistent)
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)
    payload = workflow_payload(workflow_version="1.1.0", execution_profile="tool_enabled_v1")
    response = await client.post(f"/api/v1/runs/{run['run_id']}/agent-workflows", json=payload)
    assert response.status_code == 201, response.text
    result = response.json()
    assert result["status"] == "completed", result.get("error")
    plans = [n for n in result["nodes"] if n["node_key"] == "controller_planning"]
    assert [n["input"]["workflow_round"] for n in plans] == [1, 2]
    assert [n["node_attempt"] for n in plans] == [1, 2]
    reviews = [n for n in result["nodes"] if n["node_key"] == "independent_review"]
    assert reviews[0]["transition"]["selected_transition"] == "refine"
    assert reviews[1]["transition"]["selected_transition"] == "synthesize"
    assert reviews[0]["node_execution_id"] in plans[1]["input"]["source_node_execution_ids"]
    assert provider.reviews == 2
    stats = result["loop_statistics"]
    assert stats["workflow_round_count"] == 2
    assert stats["refinement_count"] == 1
    assert stats["refinement_usage"]["model_call_count"] == 3
    assert stats["refinement_skipped_count"] == int(persistent)
    assert len(result["completion"]["task_ids"]) == 4
    assert len(set(result["completion"]["evaluation_ids"])) == 4
    async with test_database_session_factory() as db:
        attempts = list(
            await db.scalars(
                select(LlmModelAttempt).where(
                    LlmModelAttempt.run_id == run["run_id"],
                )
            )
        )
    assert result["aggregate_usage"]["model_call_count"] == len(attempts) == 7
    assert result["aggregate_usage"]["input_tokens"] == sum(a.input_tokens for a in attempts)
    reread = (
        await client.get(f"/api/v1/agent-workflows/{result['workflow_execution_id']}/result")
    ).json()
    assert reread == result
    replay = (
        await client.post(f"/api/v1/runs/{run['run_id']}/agent-workflows", json=payload)
    ).json()
    assert replay["workflow_execution_id"] == result["workflow_execution_id"]
    assert provider.reviews == 2


async def test_refinement_without_budget_is_recorded_and_final_answer_is_preserved(client):
    """Do not start a new plan when its minimum execution would consume final-answer capacity."""
    run = await create_test_run(client)
    provider = RefinementProvider()
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)
    result = (
        await client.post(
            f"/api/v1/runs/{run['run_id']}/agent-workflows",
            json=workflow_payload(workflow_version="1.1.0", max_model_calls=4),
        )
    ).json()
    assert result["status"] == "completed", result
    assert provider.reviews == 1
    assert result["loop_statistics"]["refinement_skipped_count"] == 1
    assert result["final_output"]["assistant_message_id"]


async def test_plan_repair_is_separate_from_refinement_and_accounted(client):
    """Count paid output correction separately from a business-level return to Controller."""
    run = await create_test_run(client)
    provider = RefinementProvider(repair_controller=True)
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)
    result = (
        await client.post(
            f"/api/v1/runs/{run['run_id']}/agent-workflows",
            json=workflow_payload(workflow_version="1.1.0", execution_profile="tool_enabled_v1"),
        )
    ).json()
    assert result["status"] == "completed", result
    stats = result["loop_statistics"]
    assert stats["output_repair_count"] == 1
    assert stats["output_repair_succeeded_count"] == 1
    assert stats["repair_usage"]["model_call_count"] == 1
    assert stats["refinement_count"] == 1
    assert result["aggregate_usage"]["model_call_count"] == 8
    assert "source_attempt_id" in json.dumps(stats["events"])


def test_plan_validation_counts_paid_planning_repairs_and_reserves_review_calls():
    """Reject a plan that only fits when previously consumed correction calls are ignored."""
    from nexuspilot_api.features.agent_runtime.schemas.agent_workflows import (
        AgentWorkflowCreate,
        ControllerPlanOutput,
    )
    from nexuspilot_api.features.agent_runtime.services.workflow_policy import (
        validate_controller_plan,
    )

    payload = AgentWorkflowCreate.model_validate(
        workflow_payload(
            workflow_version="1.1.0",
            execution_profile="tool_enabled_v1",
        )
    )
    plan = ControllerPlanOutput(
        decision_summary="Read within remaining capacity",
        review_policy="always",
        tasks=[
            dict(
                task_key="read",
                task_type="analysis",
                title="Read",
                objective="Read",
                assigned_role="researcher",
                expected_output_type="agent_handoff",
                completion_criteria=["Submit evidence"],
                max_model_calls=11,
            )
        ],
    )
    assert validate_controller_plan(plan, payload, used_model_calls=1).is_valid
    repaired = validate_controller_plan(plan, payload, used_model_calls=2)
    assert not repaired.is_valid
    assert repaired.total_model_call_limit == 17


@pytest.mark.parametrize("limit,field", [(1, "max_workflow_rounds"), (11, "max_nodes")])
async def test_explicit_round_or_node_limit_stops_refinement_without_losing_answer(
    client, limit, field
):
    """Preserve valid review and final synthesis when an optional second round cannot start."""
    run = await create_test_run(client)
    provider = RefinementProvider(persistent=True)
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)
    result = (
        await client.post(
            f"/api/v1/runs/{run['run_id']}/agent-workflows",
            json=workflow_payload(workflow_version="1.1.0", **{field: limit}),
        )
    ).json()
    assert result["status"] == "completed", result.get("error")
    assert provider.plans == 1
    stats = result["loop_statistics"]
    assert stats["refinement_skipped_count"] == 1
    assert stats["events"][0]["stop_reason"] == (
        "round_limit" if field == "max_workflow_rounds" else "node_budget"
    )


class InterruptedRefinementProvider(RefinementProvider):
    """Fail a second-round model call without losing first-round execution records."""

    async def generate(self, request):
        """Simulate a provider timeout after the second Controller invocation has been saved."""
        from nexuspilot_models.errors import ModelProviderError

        if request.metadata.get("workflow_round") == "2":
            raise ModelProviderError("timeout", "Refinement response could not be confirmed")
        return await super().generate(request)


async def test_refinement_failure_preserves_history_and_does_not_fake_an_answer(client):
    """Expose the unknown second round and first-round facts after refreshing the result."""
    run = await create_test_run(client)
    provider = InterruptedRefinementProvider()
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)
    result = (
        await client.post(
            f"/api/v1/runs/{run['run_id']}/agent-workflows",
            json=workflow_payload(workflow_version="1.1.0"),
        )
    ).json()
    assert result["status"] == "outcome_unknown", result.get("error")
    plans = [n for n in result["nodes"] if n["node_key"] == "controller_planning"]
    assert [n["status"] for n in plans] == ["completed", "outcome_unknown"]
    assert result["final_output"] is None
    assert result["active_node_execution_ids"] == []
    assert result["loop_statistics"]["refinement_count"] == 1
    assert result["loop_statistics"]["refinement_usage"]["model_call_count"] == 1
