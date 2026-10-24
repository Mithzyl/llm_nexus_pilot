"""Verify node transitions and format-repair restart without paid model calls."""

from datetime import timedelta

import pytest
from conftest import fake_price_catalog
from sqlalchemy import select
from test_agent_workflows import register_default_agent_test_model, scripted_registry  # noqa: F401
from test_api import create_test_run
from test_general_agent import GeneralProvider, general_model_catalog, general_payload  # noqa: F401

from nexuspilot_api.core.config import get_settings
from nexuspilot_api.features.agent_runtime.services.workflow_ownership import claim_workflow
from nexuspilot_api.features.agent_runtime.services.workflow_worker import execute_claim
from nexuspilot_api.models import LlmAgentWorkflowExecution, LlmTask
from nexuspilot_api.models.base import utc_now


async def replace_dead_owner(factory, identity):
    """Expire only the fault-injected execution after its former task has exited."""
    async with factory() as db:
        workflow = await db.get(LlmAgentWorkflowExecution, identity)
        workflow.execution_lease_expires_at = utc_now() - timedelta(seconds=1)
        await db.commit()
    return await claim_workflow(factory, identity, "replacement")


@pytest.mark.parametrize(
    "operation,key",
    [
        ("start_node", "general_agent"),
        ("start_node", "general_review"),
        ("complete_node", "general_agent"),
        ("complete_node", "general_review"),
        ("start_node", "final_synthesis"),
        ("complete_node", "final_synthesis"),
        ("start_node", "workflow_completion"),
        ("append_event", "agent.workflow.refinement.requested"),
    ],
)
async def test_node_boundary_reuses_tasks_and_committed_calls(
    client,
    monkeypatch,
    test_database_session_factory,
    operation,
    key,
):
    """Resume transitions without duplicate tasks, nodes, calls or refinement counts."""
    from nexuspilot_api.features.agent_runtime.services.workflow_execution_state_service import (
        WorkflowExecutionStateService,
    )

    class ProcessLost(BaseException):
        """Represent loss of the process rather than a handled business error."""

    original = getattr(WorkflowExecutionStateService, operation)

    async def interrupt(self, *args, **kwargs):
        """Exit once the target operation has persisted its own normal facts."""
        result = await original(self, *args, **kwargs)
        actual = (
            kwargs.get("event_type")
            if operation == "append_event"
            else kwargs.get("node_key")
            if operation == "start_node"
            else args[1].node_key
        )
        if actual == key:
            raise ProcessLost()
        return result

    monkeypatch.setattr(get_settings(), "workflow_execution_backend", "worker")
    run = await create_test_run(client)
    accepted = await client.post(
        f"/api/v1/runs/{run['run_id']}/agent-workflows", json=general_payload("always")
    )
    identity = accepted.json()["workflow_execution_id"]
    revise = operation == "append_event"
    provider = GeneralProvider(revise=revise)
    storage = test_database_session_factory.kw["info"]["body_storage"]
    claim = await claim_workflow(test_database_session_factory, identity, "first")
    with monkeypatch.context() as patch:
        patch.setattr(WorkflowExecutionStateService, operation, interrupt)
        with pytest.raises(ProcessLost):
            await execute_claim(
                test_database_session_factory,
                storage,
                scripted_registry(provider),
                fake_price_catalog(),
                claim,
            )
    claim = await replace_dead_owner(test_database_session_factory, identity)
    await execute_claim(
        test_database_session_factory,
        storage,
        scripted_registry(provider),
        fake_price_catalog(),
        claim,
    )
    result = (await client.get(f"/api/v1/agent-workflows/{identity}/result")).json()
    assert result["status"] == "completed", result.get("error")
    assert len(provider.requests) == (4 if revise else 2)
    assert result["loop_statistics"]["refinement_count"] == int(revise)
    keys = [(node["node_key"], node["input"]["workflow_round"]) for node in result["nodes"]]
    assert len(keys) == len(set(keys))
    assert result["completion"]["node_count"] == len(keys)
    async with test_database_session_factory() as db:
        tasks = list(await db.scalars(select(LlmTask).where(LlmTask.run_id == run["run_id"])))
        assert len(tasks) == (3 if revise else 2)


@pytest.mark.parametrize("cancel", [False, True])
async def test_reviewer_repair_response_is_reused_or_cancelled(
    client,
    monkeypatch,
    test_database_session_factory,
    cancel,
):
    """A committed repair response is never requested again and cancellation prevents recovery."""
    from test_review_submission import SubmittingReviewer

    from nexuspilot_api.features.agent_runtime.services.agent_model_node_service import (
        AgentModelNodeService,
    )

    class ProcessLost(BaseException):
        """Simulate loss after the format-repair request completed."""

    original = AgentModelNodeService._call_model_round

    async def interrupt(self, *args, **kwargs):
        """Leave the original completed repair attempt available for the new owner."""
        result = await original(self, *args, **kwargs)
        if kwargs.get("output_repair"):
            raise ProcessLost()
        return result

    monkeypatch.setattr(get_settings(), "workflow_execution_backend", "worker")
    run = await create_test_run(client)
    accepted = await client.post(
        f"/api/v1/runs/{run['run_id']}/agent-workflows", json=general_payload("always")
    )
    identity = accepted.json()["workflow_execution_id"]
    storage = test_database_session_factory.kw["info"]["body_storage"]
    provider = SubmittingReviewer(invalid=True)
    claim = await claim_workflow(test_database_session_factory, identity, "repair-first")
    with monkeypatch.context() as patch:
        patch.setattr(AgentModelNodeService, "_call_model_round", interrupt)
        with pytest.raises(ProcessLost):
            await execute_claim(
                test_database_session_factory,
                storage,
                scripted_registry(provider),
                fake_price_catalog(),
                claim,
            )
    if cancel:
        assert (await client.post(f"/api/v1/agent-workflows/{identity}/cancel")).status_code == 200
    claim = await replace_dead_owner(test_database_session_factory, identity)
    if not cancel:
        await execute_claim(
            test_database_session_factory,
            storage,
            scripted_registry(provider),
            fake_price_catalog(),
            claim,
        )
    else:
        assert claim is None
    result = (await client.get(f"/api/v1/agent-workflows/{identity}/result")).json()
    assert result["status"] == ("cancelled" if cancel else "completed"), result.get("error")
    assert len(provider.requests) == 3
    from nexuspilot_api.models import LlmAgentWorkflowEvent

    async with test_database_session_factory() as db:
        events = list(
            await db.scalars(
                select(LlmAgentWorkflowEvent).where(
                    LlmAgentWorkflowEvent.workflow_execution_id == identity,
                )
            )
        )
        assert (
            sum(event.event_type == "agent.node.output_repair.requested" for event in events) == 1
        )
        assert sum(
            event.event_type == "agent.node.output_repair.completed" for event in events
        ) == (0 if cancel else 1)
