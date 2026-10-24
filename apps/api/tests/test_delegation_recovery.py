"""Resume persisted delegated work without creating replacement tasks or model requests."""

import asyncio

import pytest
from conftest import fake_price_catalog
from sqlalchemy import select
from test_agent_workflows import register_default_agent_test_model, scripted_registry  # noqa: F401
from test_api import create_test_run
from test_general_agent import general_model_catalog, general_payload  # noqa: F401
from test_general_delegation import DelegatingProvider
from test_workflow_boundary_recovery import replace_dead_owner

from nexuspilot_api.core.config import get_settings
from nexuspilot_api.features.agent_runtime.services.workflow_ownership import claim_workflow
from nexuspilot_api.features.agent_runtime.services.workflow_worker import execute_claim
from nexuspilot_api.models import LlmAgentHandoff, LlmAgentRun, LlmAgentTurn, LlmTask


@pytest.mark.parametrize(
    "boundary",
    [
        "child_started",
        "child_before_model",
        "child_model",
        "child_completed",
        "handoff",
        "parent_result",
        "parent_commit",
        "agent_started",
        "turn_started",
    ],
)
@pytest.mark.parametrize("cancel", [False, True])
async def test_delegation_resumes_original_child_or_stays_cancelled(
    client,
    monkeypatch,
    test_database_session_factory,
    boundary,
    cancel,
):
    """Keep one child, Turn and Handoff; explicit cancellation must prevent further calls."""
    from nexuspilot_api.features.agent_runtime.services import (
        general_delegation_service,
        tool_loop_checkpoint,
    )
    from nexuspilot_api.features.agent_runtime.services.workflow_execution_state_service import (
        WorkflowExecutionStateService,
    )

    class ProcessLost(BaseException):
        """Simulate execution loss after persisted facts without business cleanup."""

    save = tool_loop_checkpoint.save_loop_checkpoint
    start = WorkflowExecutionStateService.start_node
    complete = WorkflowExecutionStateService.complete_node
    handoff = general_delegation_service.submit_handoff
    start_agent = WorkflowExecutionStateService.start_task_and_agent
    start_turn = WorkflowExecutionStateService.start_agent_turn

    async def interrupt_save(*args, **kwargs):
        """Stop after an exact child/parent checkpoint boundary."""
        child = args[3].node_key.startswith("general_delegate.")
        if boundary == "parent_commit" and not child and kwargs["phase"] == "tool_result":
            raise ProcessLost()
        result = await save(*args, **kwargs)
        if (
            (boundary == "child_before_model" and child and kwargs["phase"] == "before_model")
            or (boundary == "child_model" and child and kwargs["phase"] == "model_response")
            or (boundary == "parent_result" and not child and kwargs["phase"] == "tool_result")
        ):
            raise ProcessLost()
        return result

    async def interrupt_start(self, *args, **kwargs):
        """Leave the child node committed but no child attempt started."""
        node = await start(self, *args, **kwargs)
        if boundary == "child_started" and node.node_key.startswith("general_delegate."):
            raise ProcessLost()
        return node

    async def interrupt_complete(self, workflow, node, **kwargs):
        """Leave a child's final candidate committed before handoff bookkeeping."""
        result = await complete(self, workflow, node, **kwargs)
        if boundary == "child_completed" and node.node_key.startswith("general_delegate."):
            raise ProcessLost()
        return result

    async def interrupt_handoff(*args, **kwargs):
        """Leave the original idempotent handoff saved before the parent tool result."""
        result = await handoff(*args, **kwargs)
        if boundary == "handoff":
            raise ProcessLost()
        return result

    async def interrupt_agent(self, *args, **kwargs):
        """Stop after the child Task/Agent Run exists but before its node is created."""
        result = await start_agent(self, *args, **kwargs)
        if boundary == "agent_started":
            raise ProcessLost()
        return result

    async def interrupt_turn(self, *args, **kwargs):
        """Stop after the first Turn exists and before its model dispatch."""
        result = await start_turn(self, *args, **kwargs)
        if boundary == "turn_started":
            raise ProcessLost()
        return result

    monkeypatch.setattr(get_settings(), "workflow_execution_backend", "worker")
    run = await create_test_run(client)
    payload = general_payload("never")
    payload["role_bindings"]["planner"] = payload["role_bindings"]["controller"].copy()
    accepted = await client.post(f"/api/v1/runs/{run['run_id']}/agent-workflows", json=payload)
    identity = accepted.json()["workflow_execution_id"]
    provider = DelegatingProvider()
    storage = test_database_session_factory.kw["info"]["body_storage"]
    first = await claim_workflow(test_database_session_factory, identity, "delegation-first")
    with monkeypatch.context() as patch:
        patch.setattr(tool_loop_checkpoint, "save_loop_checkpoint", interrupt_save)
        patch.setattr(WorkflowExecutionStateService, "start_node", interrupt_start)
        patch.setattr(WorkflowExecutionStateService, "complete_node", interrupt_complete)
        patch.setattr(general_delegation_service, "submit_handoff", interrupt_handoff)
        patch.setattr(WorkflowExecutionStateService, "start_task_and_agent", interrupt_agent)
        patch.setattr(WorkflowExecutionStateService, "start_agent_turn", interrupt_turn)
        with pytest.raises(ProcessLost):
            await execute_claim(
                test_database_session_factory,
                storage,
                scripted_registry(provider),
                fake_price_catalog(),
                first,
            )
    calls_before = len(provider.requests)
    if cancel:
        assert (await client.post(f"/api/v1/agent-workflows/{identity}/cancel")).status_code == 200
    second = await replace_dead_owner(test_database_session_factory, identity)
    if cancel:
        assert second is None
    else:
        await execute_claim(
            test_database_session_factory,
            storage,
            scripted_registry(provider),
            fake_price_catalog(),
            second,
        )
    result = (await client.get(f"/api/v1/agent-workflows/{identity}/result")).json()
    assert result["status"] == ("cancelled" if cancel else "completed"), result.get("error")
    assert len(provider.requests) == (calls_before if cancel else 3)
    async with test_database_session_factory() as db:
        tasks = list(await db.scalars(select(LlmTask).where(LlmTask.run_id == run["run_id"])))
        agents = list(
            await db.scalars(select(LlmAgentRun).where(LlmAgentRun.run_id == run["run_id"]))
        )
        handoffs = list(
            await db.scalars(select(LlmAgentHandoff).where(LlmAgentHandoff.run_id == run["run_id"]))
        )
        assert len(tasks) == 2 and len(agents) == 1
        if not cancel:
            assert len(handoffs) == 1
            turns = list(
                await db.scalars(
                    select(LlmAgentTurn).where(LlmAgentTurn.agent_run_id == agents[0].agent_run_id)
                )
            )
            assert len(turns) == 1


@pytest.mark.parametrize("cancel", [True, False])
async def test_inflight_child_cancel_or_unknown_does_not_replay(
    client,
    monkeypatch,
    test_database_session_factory,
    cancel,
):
    """Cancel a live child; after process loss never resend its unconfirmed model request."""

    class ProcessLost(BaseException):
        """Leave a started model attempt without a confirmed response."""

    started = asyncio.Event()

    class InterruptedChild(DelegatingProvider):
        """Keep the parent deterministic and interrupt only the delegated invocation."""

        async def generate(self, request):
            """Expose the child dispatch so cancellation or process loss occurs in flight."""
            if request.metadata["agent_role"] != "planner":
                return await super().generate(request)
            self.requests.append(request)
            started.set()
            if not cancel:
                raise ProcessLost()
            await asyncio.Event().wait()

    monkeypatch.setattr(get_settings(), "workflow_execution_backend", "worker")
    run = await create_test_run(client)
    payload = general_payload("never")
    payload["role_bindings"]["planner"] = payload["role_bindings"]["controller"].copy()
    accepted = await client.post(f"/api/v1/runs/{run['run_id']}/agent-workflows", json=payload)
    identity = accepted.json()["workflow_execution_id"]
    provider = InterruptedChild()
    storage = test_database_session_factory.kw["info"]["body_storage"]
    claim = await claim_workflow(test_database_session_factory, identity, "child-live")
    if cancel:
        task = asyncio.create_task(
            execute_claim(
                test_database_session_factory,
                storage,
                scripted_registry(provider),
                fake_price_catalog(),
                claim,
            )
        )
        try:
            await asyncio.wait_for(started.wait(), 5)
            assert (
                await client.post(f"/api/v1/agent-workflows/{identity}/cancel")
            ).status_code == 200
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 8)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    else:
        with pytest.raises(ProcessLost):
            await execute_claim(
                test_database_session_factory,
                storage,
                scripted_registry(provider),
                fake_price_catalog(),
                claim,
            )
        replacement = await replace_dead_owner(test_database_session_factory, identity)
        await execute_claim(
            test_database_session_factory,
            storage,
            scripted_registry(provider),
            fake_price_catalog(),
            replacement,
        )
    result = (await client.get(f"/api/v1/agent-workflows/{identity}/result")).json()
    assert result["status"] == ("cancelled" if cancel else "outcome_unknown")
    assert result["final_output"] is None
    assert len(provider.requests) == 2
