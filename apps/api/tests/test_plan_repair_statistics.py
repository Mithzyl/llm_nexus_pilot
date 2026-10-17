"""Exercise plan repair accounting and two business rounds with local scripted providers."""

import json
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from test_agent_workflows import (
    register_default_agent_test_model,  # noqa: F401
    scripted_registry,
    workflow_payload,
)
from test_api import create_test_run
from test_controller_plan_repair import InterruptedPlanRepairProvider, PlanRepairProvider
from test_workflow_refinement import RefinementProvider

from nexuspilot_api.core.dependencies import get_provider_registry
from nexuspilot_api.features.agent_runtime.services.workflow_loop_statistics import (
    read_plan_repair_statistics,
)
from nexuspilot_api.main import app
from nexuspilot_api.models import LlmAgentWorkflowEvent, LlmModelAttempt
from nexuspilot_api.models.enums import AgentWorkflowNodeStatus, AgentWorkflowStatus


class MisleadingRepairNarrativeProvider(PlanRepairProvider):
    """Put a false process claim in model-authored results without changing platform events."""

    def _node_output(self, node_key, request):
        """Keep valid node fields but claim seventeen repairs in the upstream summary."""
        output = super()._node_output(node_key, request)
        if node_key == "worker_execution":
            output["summary"] = "The platform performed seventeen plan repairs."
        return output


@pytest.mark.parametrize("invalid_mode", [None, "duplicate_key"])
async def test_final_synthesis_receives_durable_execution_facts(client, invalid_mode):
    """Final instructions and inputs distinguish real repairs from model-written narratives."""
    run = await create_test_run(client)
    provider = MisleadingRepairNarrativeProvider(invalid_mode=invalid_mode)
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)
    response = await client.post(
        f"/api/v1/runs/{run['run_id']}/agent-workflows",
        json=workflow_payload(workflow_version="1.1.0"),
    )
    assert response.status_code == 201, response.text
    result = response.json()
    assert result["status"] == "completed"
    request = next(
        r for r in provider.requests if r.metadata["agent_node_key"] == "final_synthesis"
    )
    inputs = json.loads(request.messages[-1].content)
    assert inputs["execution_facts"]["loop_statistics"] == result["loop_statistics"]
    assert inputs["execution_facts"]["loop_statistics"]["plan_repair_count"] == int(
        bool(invalid_mode)
    )
    assert "Model narratives are not execution evidence" in request.system_instruction
    assert any("seventeen plan repairs" in message.content for message in request.messages)


@pytest.mark.parametrize(
    "mode,persistent,options,requested,calls,succeeded,failed,skipped,unknown",
    [
        ("duplicate_key", False, {}, 1, 1, 1, 0, 0, 0),
        ("duplicate_key", True, {}, 1, 1, 0, 1, 0, 0),
        ("forbidden", False, {}, 0, 0, 0, 0, 1, 0),
        ("duplicate_key", False, {"max_model_calls": 6}, 0, 0, 0, 0, 1, 0),
        ("duplicate_key", False, {"workflow_version": "1.0.0"}, 0, 0, 0, 0, 0, 0),
        (None, False, {}, 0, 0, 0, 0, 0, 0),
        ("timeout", False, {}, 1, 1, 0, 0, 0, 1),
    ],
)
async def test_plan_statistics_distinguish_execution_outcomes(
    client, mode, persistent, options, requested, calls, succeeded, failed, skipped, unknown
):
    """Separate an unexecuted repair from a failed validation or unknown paid invocation."""
    run = await create_test_run(client)
    provider = (
        InterruptedPlanRepairProvider(invalid_mode="duplicate_key")
        if mode == "timeout"
        else PlanRepairProvider(invalid_mode=mode, persistent=persistent)
    )
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)
    payload = workflow_payload(
        **{
            "workflow_version": "1.1.0",
            "execution_profile": "tool_enabled_v1",
            **options,
        }
    )
    url = f"/api/v1/runs/{run['run_id']}/agent-workflows"
    response = await client.post(url, json=payload)
    assert response.status_code == 201, response.text
    result = response.json()
    stats = result["loop_statistics"]
    for suffix, expected in {
        "requested_count": requested,
        "count": calls,
        "succeeded_count": succeeded,
        "failed_count": failed,
        "skipped_count": skipped,
        "unknown_count": unknown,
        "pending_count": 0,
        "cancelled_count": 0,
    }.items():
        assert stats[f"plan_repair_{suffix}"] == expected
    assert stats["plan_repair_usage"]["model_call_count"] == calls
    assert len(stats["plan_repair_attempt_ids"]) == calls
    assert stats["workflow_round_count"] == 1
    assert stats["refinement_count"] == 0
    reread = await client.get(f"/api/v1/agent-workflows/{result['workflow_execution_id']}/result")
    assert reread.json()["loop_statistics"] == stats
    repeated = await client.post(url, json=payload)
    assert repeated.json()["loop_statistics"] == stats


class TwoRoundPlanRepairProvider(RefinementProvider):
    """Reject each round's initial plan, then optionally require repair JSON correction."""

    def __init__(self, *, repair_rounds, malformed_repair=False):
        """Choose business rounds requiring repair and track one optional schema failure."""
        super().__init__()
        self.repair_rounds = repair_rounds
        self.malformed_repair = malformed_repair
        self.format_failure_sent = False

    def _node_output(self, node_key, request):
        """Combine Reviewer refinement with deterministic duplicate-task plan rejection."""
        output = super()._node_output(node_key, request)
        if node_key == "controller_planning":
            inputs = json.loads(request.messages[-1].content)
            round_number = int(request.metadata["workflow_round"])
            if round_number in self.repair_rounds and "plan_repair" not in inputs:
                output["tasks"].append(output["tasks"][0].copy())
            elif self.malformed_repair and not self.format_failure_sent:
                self.format_failure_sent = True
                output.pop("decision_summary")
        return output


class FailedSecondRoundRepairProvider(TwoRoundPlanRepairProvider):
    """Fail the second business round's repair without changing its first-round evidence."""

    def __init__(self, failure_mode):
        """Select persistent business rejection or an indeterminate provider timeout."""
        super().__init__(repair_rounds={1, 2})
        self.failure_mode = failure_mode

    async def generate(self, request):
        """Interrupt only the second-round repair after its request has been persisted."""
        from nexuspilot_models.errors import ModelProviderError

        inputs = json.loads(request.messages[-1].content)
        if (
            self.failure_mode == "timeout"
            and request.metadata.get("workflow_round") == "2"
            and "plan_repair" in inputs
        ):
            raise ModelProviderError("timeout", "Second-round repair result is unknown")
        return await super().generate(request)

    def _node_output(self, node_key, request):
        """Keep the second-round repaired plan illegal to exercise the one-repair limit."""
        output = super()._node_output(node_key, request)
        if (
            self.failure_mode == "invalid"
            and node_key == "controller_planning"
            and request.metadata.get("workflow_round") == "2"
            and "plan_repair" in json.loads(request.messages[-1].content)
        ):
            output["tasks"].append(output["tasks"][0].copy())
        return output


@pytest.mark.parametrize("mode,status", [("invalid", "failed"), ("timeout", "outcome_unknown")])
async def test_second_round_repair_failure_keeps_first_success(client, mode, status):
    """Retain first-round repair success while reporting the failed/unknown second repair."""
    run = await create_test_run(client)
    provider = FailedSecondRoundRepairProvider(mode)
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)
    payload = workflow_payload(workflow_version="1.1.0", execution_profile="tool_enabled_v1")
    url = f"/api/v1/runs/{run['run_id']}/agent-workflows"
    response = await client.post(url, json=payload)
    assert response.status_code == 201, response.text
    result = response.json()
    assert result["status"] == status, result.get("error")
    assert result["final_output"] is None
    stats = result["loop_statistics"]
    assert stats["workflow_round_count"] == 2
    assert stats["refinement_count"] == 1
    assert stats["plan_repair_requested_count"] == stats["plan_repair_count"] == 2
    assert stats["plan_repair_succeeded_count"] == 1
    assert stats["plan_repair_failed_count"] == int(mode == "invalid")
    assert stats["plan_repair_unknown_count"] == int(mode == "timeout")
    assert stats["plan_repair_pending_count"] == stats["plan_repair_skipped_count"] == 0
    dispatches = [n for n in result["nodes"] if n["node_key"] == "agent_dispatch"]
    assert [n["input"]["workflow_round"] for n in dispatches] == [1]
    assert stats["refinement_usage"]["model_call_count"] == 2
    assert result["aggregate_usage"]["model_call_count"] == 6
    reread = await client.get(f"/api/v1/agent-workflows/{result['workflow_execution_id']}/result")
    assert reread.json() == result
    calls = len(provider.requests)
    repeated = await client.post(url, json=payload)
    assert repeated.json() == result
    assert len(provider.requests) == calls


@pytest.mark.parametrize(
    "repair_rounds,malformed", [({1}, False), ({2}, False), ({1, 2}, False), ({1}, True)]
)
async def test_plan_repair_and_two_rounds_preserve_history_and_usage(
    client, test_database_session_factory, repair_rounds, malformed
):
    """Keep same-round repairs separate from refinement and reconcile usage with saved Attempts."""
    run = await create_test_run(client)
    provider = TwoRoundPlanRepairProvider(repair_rounds=repair_rounds, malformed_repair=malformed)
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)
    payload = workflow_payload(workflow_version="1.1.0", execution_profile="tool_enabled_v1")
    url = f"/api/v1/runs/{run['run_id']}/agent-workflows"
    response = await client.post(url, json=payload)
    assert response.status_code == 201, response.text
    result = response.json()
    assert result["status"] == "completed", result.get("error")
    stats = result["loop_statistics"]
    assert stats["workflow_round_count"] == 2
    assert stats["refinement_count"] == 1
    assert stats["plan_repair_requested_count"] == len(repair_rounds)
    assert stats["plan_repair_succeeded_count"] == len(repair_rounds)
    assert stats["plan_repair_count"] == len(repair_rounds) + int(malformed)
    assert stats["output_repair_count"] == int(malformed)
    plans = [n for n in result["nodes"] if n["node_key"] == "controller_planning"]
    assert [n["input"]["workflow_round"] for n in plans] == [
        r for r in (1, 2) for _ in range(1 + int(r in repair_rounds))
    ]
    assert len({n["node_execution_id"] for n in plans}) == len(plans)
    assert [n["node_attempt"] for n in plans] == list(range(1, len(plans) + 1))
    assert provider.reviews == 2
    assert result["final_output"]["assistant_message_id"]
    assert len(result["completion"]["task_ids"]) == 4
    plan_ids = {n["node_execution_id"] for n in plans}
    repair_sequences = {
        n["node_sequence"]
        for n in plans
        if plan_ids.intersection(n["input"]["source_node_execution_ids"])
    }
    assert len(repair_sequences) == len(repair_rounds)
    async with test_database_session_factory() as db:
        attempts = list(
            await db.scalars(select(LlmModelAttempt).where(LlmModelAttempt.run_id == run["run_id"]))
        )
    repair_attempts = [a for a in attempts if int(a.request_key.split(":")[2]) in repair_sequences]
    assert set(stats["plan_repair_attempt_ids"]) == {a.attempt_id for a in repair_attempts}
    assert (
        result["aggregate_usage"]["model_call_count"]
        == len(attempts)
        == 7 + len(repair_rounds) + int(malformed)
    )
    for field in ("input_tokens", "output_tokens", "cached_tokens", "estimated_cost"):
        assert Decimal(str(stats["plan_repair_usage"][field])) == sum(
            (getattr(a, field) or 0 for a in repair_attempts), Decimal(0)
        )
        assert Decimal(str(result["aggregate_usage"][field])) == sum(
            (getattr(a, field) or 0 for a in attempts), Decimal(0)
        )
    assert stats["refinement_usage"]["model_call_count"] == 3 + int(2 in repair_rounds)
    calls_before = len(provider.requests)
    reread = await client.get(f"/api/v1/agent-workflows/{result['workflow_execution_id']}/result")
    repeated = await client.post(url, json=payload)
    assert reread.json() == repeated.json() == result
    assert len(provider.requests) == calls_before


@pytest.mark.parametrize(
    "node_status,workflow_status,has_attempt,outcome",
    [
        ("running", "running", False, "pending"),
        ("completed", "running", True, "pending"),
        ("cancelled", "cancelled", False, "cancelled"),
        ("failed", "failed", True, "failed"),
        ("completed", "failed", True, "failed"),
    ],
)
async def test_incomplete_plan_repair_snapshot_is_not_success(
    client, test_database_session_factory, node_status, workflow_status, has_attempt, outcome
):
    """Read intermediate/cancelled snapshots against real Attempts without inventing success.

    Snapshot statuses are synthetic; persisted request events and call records
    come from the API. This does not exercise cancellation propagation itself.
    """
    run = await create_test_run(client)
    provider = PlanRepairProvider(invalid_mode="duplicate_key")
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)
    result = (
        await client.post(
            f"/api/v1/runs/{run['run_id']}/agent-workflows",
            json=workflow_payload(workflow_version="1.1.0"),
        )
    ).json()
    repair = [n for n in result["nodes"] if n["node_key"] == "controller_planning"][1]
    workflow = SimpleNamespace(
        workflow_execution_id=result["workflow_execution_id"],
        run_id=run["run_id"],
        status=AgentWorkflowStatus(workflow_status),
    )
    node = SimpleNamespace(
        node_execution_id=repair["node_execution_id"],
        node_sequence=repair["node_sequence"] if has_attempt else 999,
        status=AgentWorkflowNodeStatus(node_status),
    )
    async with test_database_session_factory() as db:
        events = list(
            await db.scalars(
                select(LlmAgentWorkflowEvent).where(
                    LlmAgentWorkflowEvent.workflow_execution_id == workflow.workflow_execution_id,
                    LlmAgentWorkflowEvent.event_type == "agent.controller.plan_repair.requested",
                )
            )
        )
        stats = await read_plan_repair_statistics(db, workflow, [node], events)
    assert stats["plan_repair_requested_count"] == 1
    assert stats["plan_repair_count"] == int(has_attempt)
    assert stats["plan_repair_succeeded_count"] == 0
    for state in ("pending", "cancelled", "failed", "unknown"):
        assert stats[f"plan_repair_{state}_count"] == int(state == outcome)
