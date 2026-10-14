"""Verify bounded Controller plan repair and durable accounting without paid calls."""

import json

import pytest
from test_agent_workflows import (
    ScriptedAgentProvider,
    register_default_agent_test_model,  # noqa: F401
    scripted_registry,
    workflow_payload,
)
from test_api import create_test_run

from nexuspilot_api.core.dependencies import get_provider_registry
from nexuspilot_api.main import app


class PlanRepairProvider(ScriptedAgentProvider):
    """Return an invalid plan once, then a valid plan, preserving normal node schemas."""

    def __init__(self, *, invalid_mode=None, persistent=False):
        """Track plan attempts and optional persistent failure."""
        super().__init__()
        self.invalid_mode = invalid_mode
        self.persistent = persistent
        self.plans = 0
        self.plan_inputs = []

    def _node_output(self, node_key, request):
        """Inject invalid plans for Controller repair without reviewer business rework."""
        output = super()._node_output(node_key, request)
        if node_key == "controller_planning":
            self.plans += 1
            self.plan_inputs.append(json.loads(request.messages[-1].content))
            if self.invalid_mode and (self.plans == 1 or self.persistent):
                if self.invalid_mode == "duplicate_key":
                    output["tasks"].append(output["tasks"][0].copy())
                elif self.invalid_mode == "self_dep":
                    first_key = output["tasks"][0]["task_key"]
                    output["dependencies"] = [
                        {"task_key": first_key, "depends_on_task_key": first_key}
                    ]
                elif self.invalid_mode == "review_policy":
                    output["review_policy"] = "never"
                elif self.invalid_mode == "forbidden":
                    output["tasks"][0]["required_capabilities"] = ["write"]
                elif self.invalid_mode == "over_budget":
                    output["tasks"][0]["max_model_calls"] = 16
        return output


class InterruptedPlanRepairProvider(PlanRepairProvider):
    """Return one invalid plan, then fail the repair request with an unknown outcome."""

    async def generate(self, request):
        """Simulate a timeout only after the first Controller result has been persisted."""
        from nexuspilot_models.errors import ModelProviderError

        if request.metadata["agent_node_key"] == "controller_planning" and self.plans == 1:
            raise ModelProviderError("timeout", "Repair response could not be confirmed")
        return await super().generate(request)


async def test_plan_repair_timeout_preserves_nodes_without_dispatch(client):
    """Retain rejected plan and unknown repair attempt without issuing another repair."""
    run = await create_test_run(client)
    provider = InterruptedPlanRepairProvider(invalid_mode="duplicate_key")
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)
    response = await client.post(
        f"/api/v1/runs/{run['run_id']}/agent-workflows",
        json=workflow_payload(workflow_version="1.1.0"),
    )
    assert response.status_code == 201, response.text
    result = response.json()
    assert result["status"] == "outcome_unknown", result.get("error")
    plans = [n for n in result["nodes"] if n["node_key"] == "controller_planning"]
    assert [n["status"] for n in plans] == ["completed", "outcome_unknown"]
    assert result["active_node_execution_ids"] == []
    assert result["final_output"] is None
    assert not any(n["node_key"] == "agent_dispatch" for n in result["nodes"])


@pytest.mark.parametrize(
    "invalid_mode", ["duplicate_key", "self_dep", "review_policy", "over_budget"]
)
async def test_controller_plan_repair_once_and_preserves_round(client, invalid_mode):
    """Retry once after invalid plan, complete normally with repair accounting."""
    run = await create_test_run(client)
    provider = PlanRepairProvider(invalid_mode=invalid_mode)
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)
    payload = workflow_payload(workflow_version="1.1.0", execution_profile="tool_enabled_v1")
    response = await client.post(f"/api/v1/runs/{run['run_id']}/agent-workflows", json=payload)
    assert response.status_code == 201, response.text
    result = response.json()
    assert result["status"] == "completed", result.get("error")
    plans = [n for n in result["nodes"] if n["node_key"] == "controller_planning"]
    assert len(plans) == 2
    assert [n["input"]["workflow_round"] for n in plans] == [1, 1]
    assert provider.plans == 2
    validations = [n for n in result["nodes"] if n["node_key"] == "plan_validation"]
    assert [n["output"]["is_valid"] for n in validations] == [False, True]
    assert validations[0]["transition"]["next_node_keys"] == ["controller_planning"]
    assert validations[0]["transition"]["skipped_node_keys"] == []
    dispatches = [n for n in result["nodes"] if n["node_key"] == "agent_dispatch"]
    assert dispatches[0]["node_sequence"] > validations[1]["node_sequence"]
    assert validations[0]["input"]["source_node_execution_ids"] == [plans[0]["node_execution_id"]]
    assert validations[1]["input"]["source_node_execution_ids"] == [plans[1]["node_execution_id"]]
    assert validations[0]["node_execution_id"] in plans[1]["input"]["source_node_execution_ids"]
    assert [n["node_attempt"] for n in plans] == [1, 2]
    repair_input = provider.plan_inputs[1]["plan_repair"]
    assert repair_input["original_plan"] == plans[0]["output"]
    assert repair_input["validation"] == validations[0]["output"]
    assert provider.plan_inputs[1]["workflow_round"] == 1
    assert "refinement" in provider.plan_inputs[1]
    assert result["loop_statistics"]["workflow_round_count"] == 1
    assert result["loop_statistics"]["refinement_count"] == 0
    assert result["aggregate_usage"]["model_call_count"] == 5
    reread = (
        await client.get(f"/api/v1/agent-workflows/{result['workflow_execution_id']}/result")
    ).json()
    assert reread == result


async def test_persistent_invalid_plan_fails_without_dispatch(client):
    """Fail persistently without dispatching tasks."""
    run = await create_test_run(client)
    provider = PlanRepairProvider(invalid_mode="duplicate_key", persistent=True)
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)
    result = (
        await client.post(
            f"/api/v1/runs/{run['run_id']}/agent-workflows",
            json=workflow_payload(workflow_version="1.1.0", execution_profile="tool_enabled_v1"),
        )
    ).json()
    assert result["status"] == "failed", result.get("error")
    assert provider.plans == 2
    assert [
        n["output"]["is_valid"] for n in result["nodes"] if n["node_key"] == "plan_validation"
    ] == [False, False]
    assert not any(n["node_key"] == "agent_dispatch" for n in result["nodes"])


@pytest.mark.parametrize(
    "options",
    [
        {"execution_profile": "tool_enabled_v1", "max_model_calls": 7, "max_nodes": 13},
        {"execution_profile": "model_only_v1", "review_policy": "never", "max_model_calls": 4},
    ],
)
async def test_plan_repair_uses_available_minimum_completion_budget(client, options):
    """Repair at the exact resource boundary using the request's review reservation."""
    run = await create_test_run(client)
    provider = PlanRepairProvider(invalid_mode="duplicate_key")
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)
    response = await client.post(
        f"/api/v1/runs/{run['run_id']}/agent-workflows",
        json=workflow_payload(workflow_version="1.1.0", **options),
    )
    assert response.status_code == 201, response.text
    result = response.json()
    assert result["status"] == "completed", result.get("error")
    assert provider.plans == 2
    assert len(result["nodes"]) <= options.get("max_nodes", 32)
    repair_input = provider.plan_inputs[1]
    assert repair_input["remaining_model_calls"] == options["max_model_calls"] - 1
    assert repair_input["remaining_nodes"] == options.get("max_nodes", 32) - 4
    assert repair_input["remaining_wall_time_ms"] > 0
    if options["execution_profile"] == "model_only_v1":
        assert "tool_guidance" not in repair_input


async def test_old_version_1_0_fails_without_repair(client):
    """Old 1.0 workflow does not trigger repair, fails immediately."""
    run = await create_test_run(client)
    provider = PlanRepairProvider(invalid_mode="duplicate_key")
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)
    result = (
        await client.post(
            f"/api/v1/runs/{run['run_id']}/agent-workflows",
            json=workflow_payload(workflow_version="1.0.0", execution_profile="tool_enabled_v1"),
        )
    ).json()
    assert result["status"] == "failed", result.get("error")
    plans = [n for n in result["nodes"] if n["node_key"] == "controller_planning"]
    assert len(plans) == 1
    assert provider.plans == 1


@pytest.mark.parametrize(
    "mode,limits",
    [
        ("forbidden", {}),
        ("duplicate_key", {"max_model_calls": 6}),
        ("duplicate_key", {"max_nodes": 11}),
    ],
)
async def test_plan_repair_skips_forbidden_or_insufficient_resources(client, mode, limits):
    """Never spend a repair call on forbidden capabilities or insufficient remaining resources."""
    run = await create_test_run(client)
    provider = PlanRepairProvider(invalid_mode=mode)
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)
    response = await client.post(
        f"/api/v1/runs/{run['run_id']}/agent-workflows",
        json=workflow_payload(
            workflow_version="1.1.0", execution_profile="tool_enabled_v1", **limits
        ),
    )
    assert response.status_code == 201, response.text
    result = response.json()
    assert result["status"] == "failed"
    assert provider.plans == 1
    assert not any(n["node_key"] == "agent_dispatch" for n in result["nodes"])
