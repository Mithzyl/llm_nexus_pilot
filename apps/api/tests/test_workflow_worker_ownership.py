"""Exercise queued acceptance and exclusive ownership without paid model calls."""

import asyncio
from datetime import timedelta

import pytest
from conftest import fake_price_catalog
from sqlalchemy import select
from test_agent_workflows import register_default_agent_test_model, scripted_registry  # noqa: F401
from test_api import create_test_run
from test_general_agent import GeneralProvider, general_model_catalog, general_payload  # noqa: F401

from nexuspilot_api.core.config import get_settings
from nexuspilot_api.core.errors import ResourceConflictError
from nexuspilot_api.models import LlmAgentWorkflowExecution, LlmOutboxEvent
from nexuspilot_api.models.base import utc_now


async def test_queued_workflow_has_one_outbox_and_one_owner(
    client, monkeypatch, test_database_session_factory,
):
    """Duplicate acceptance keeps one job; an unexpired owner excludes another claimant."""
    from nexuspilot_api.features.agent_runtime.services.workflow_ownership import claim_workflow
    monkeypatch.setattr(get_settings(), "workflow_execution_backend", "worker")
    run = await create_test_run(client)
    endpoint = f"/api/v1/runs/{run['run_id']}/agent-workflows"
    first = await client.post(endpoint, json=general_payload("never"))
    assert first.status_code == 201, first.text
    assert first.json()["status"] == "pending"
    workflow_id = first.json()["workflow_execution_id"]
    second = await client.post(endpoint, json=general_payload("never"))
    assert second.json()["workflow_execution_id"] == workflow_id
    async with test_database_session_factory() as db:
        outbox = list(await db.scalars(select(LlmOutboxEvent).where(
            LlmOutboxEvent.aggregate_id == workflow_id)))
        assert len(outbox) == 1
    one = await claim_workflow(test_database_session_factory, workflow_id, "worker-a")
    assert one is not None and one.version == 1
    assert await claim_workflow(test_database_session_factory, workflow_id, "worker-b") is None
    async with test_database_session_factory() as db:
        workflow = await db.get(LlmAgentWorkflowExecution, workflow_id)
        workflow.execution_lease_expires_at = utc_now() - timedelta(seconds=1)
        await db.commit()
    two = await claim_workflow(test_database_session_factory, workflow_id, "worker-b")
    assert two is not None and two.version == 2
    async with test_database_session_factory() as db:
        db.info["workflow_execution_claim"] = one
        workflow = await db.get(LlmAgentWorkflowExecution, workflow_id)
        workflow.current_stage = "stale-write"
        with pytest.raises(ResourceConflictError):
            await db.commit()
    from nexuspilot_api.features.agent_runtime.services.workflow_worker import execute_claim
    provider = GeneralProvider()
    await execute_claim(test_database_session_factory,
        test_database_session_factory.kw["info"]["body_storage"],
        scripted_registry(provider), fake_price_catalog(), two)
    result = await client.get(f"/api/v1/agent-workflows/{workflow_id}/result")
    assert result.json()["status"] == "completed", result.text
    assert result.json()["final_output"]["final_text"]
    assert len(provider.requests) == 1
    assert await claim_workflow(test_database_session_factory, workflow_id, "worker-c") is None


@pytest.mark.parametrize("external_intent", ["none", "model", "tool"])
async def test_worker_does_not_replay_unrestorable_execution(
    client, monkeypatch, test_database_session_factory, external_intent,
):
    """A saved node without a restorable checkpoint never starts a replacement model call."""
    from nexuspilot_api.features.agent_runtime.services.workflow_definition_registry import (
        DEFAULT_WORKFLOW_DEFINITION_REGISTRY,
    )
    from nexuspilot_api.features.agent_runtime.services.workflow_execution_state_service import (
        WorkflowExecutionStateService,
    )
    from nexuspilot_api.features.agent_runtime.services.workflow_ownership import claim_workflow
    from nexuspilot_api.features.agent_runtime.services.workflow_worker import execute_claim
    from nexuspilot_api.models import AttemptStatus, LlmModelAttempt, LlmModelToolCall
    monkeypatch.setattr(get_settings(), "workflow_execution_backend", "worker")
    run = await create_test_run(client)
    response = await client.post(f"/api/v1/runs/{run['run_id']}/agent-workflows",
                                 json=general_payload("never"))
    workflow_id = response.json()["workflow_execution_id"]
    first = await claim_workflow(test_database_session_factory, workflow_id, "worker-a")
    async with test_database_session_factory() as db:
        db.info["workflow_execution_claim"] = first
        workflow = await db.get(LlmAgentWorkflowExecution, workflow_id)
        state = WorkflowExecutionStateService(db_session=db,
            workflow_definitions=DEFAULT_WORKFLOW_DEFINITION_REGISTRY)
        await state.start_node(workflow, node_key="general_agent", node_type="model",
            output_type="general_candidate", input_values={},
            status_label="Working", summary="Working")
        if external_intent != "none":
            attempt = LlmModelAttempt(run_id=run["run_id"], provider="openai", model="test-model",
                status=(AttemptStatus.STARTED if external_intent == "model"
                        else AttemptStatus.COMPLETED))
            db.add(attempt)
            await db.flush()
            if external_intent == "tool":
                db.add(LlmModelToolCall(attempt_id=attempt.attempt_id, tool_name="run_python",
                    risk_level="code_execution", input_json={}, status="running"))
            await db.commit()
    async with test_database_session_factory() as db:
        workflow = await db.get(LlmAgentWorkflowExecution, workflow_id)
        workflow.execution_lease_expires_at = utc_now() - timedelta(seconds=1)
        await db.commit()
    second = await claim_workflow(test_database_session_factory, workflow_id, "worker-b")
    provider = GeneralProvider()
    await execute_claim(test_database_session_factory,
        test_database_session_factory.kw["info"]["body_storage"],
        scripted_registry(provider), fake_price_catalog(), second)
    result = (await client.get(f"/api/v1/agent-workflows/{workflow_id}/result")).json()
    assert result["status"] == ("failed" if external_intent == "none" else "outcome_unknown")
    assert result["error"]["error_code"] == "workflow_checkpoint_recovery_unavailable"
    assert not provider.requests


async def test_worker_observes_explicit_cancel(client, monkeypatch, test_database_session_factory):
    """A persisted cancel interrupts a running fixed Provider and cannot publish an answer."""
    from nexuspilot_api.features.agent_runtime.services.workflow_ownership import claim_workflow
    from nexuspilot_api.features.agent_runtime.services.workflow_worker import execute_claim
    monkeypatch.setattr(get_settings(), "workflow_execution_backend", "worker")
    run = await create_test_run(client)
    response = await client.post(f"/api/v1/runs/{run['run_id']}/agent-workflows",
                                 json=general_payload("never"))
    workflow_id = response.json()["workflow_execution_id"]
    claim = await claim_workflow(test_database_session_factory, workflow_id, "worker-a")
    started = asyncio.Event()

    class WaitingProvider(GeneralProvider):
        """Observe a dispatched invocation without an external model request."""

        async def generate(self, request):
            """Block until Worker cancellation propagates into the invocation."""
            self.requests.append(request)
            started.set()
            await asyncio.Event().wait()

    provider = WaitingProvider()
    task = asyncio.create_task(execute_claim(test_database_session_factory,
        test_database_session_factory.kw["info"]["body_storage"],
        scripted_registry(provider), fake_price_catalog(), claim))
    try:
        await asyncio.wait_for(started.wait(), 5)
        cancelled = await client.post(f"/api/v1/agent-workflows/{workflow_id}/cancel")
        assert cancelled.status_code == 200
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 8)
        result = (await client.get(f"/api/v1/agent-workflows/{workflow_id}/result")).json()
        assert result["status"] == "cancelled" and result["final_output"] is None
        assert len(provider.requests) == 1
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_real_mysql_claim_race(client, monkeypatch, test_database_session_factory):
    """Actual MySQL row locking must admit exactly one concurrent Worker claimant."""
    from nexuspilot_api.features.agent_runtime.services.workflow_ownership import claim_workflow
    if test_database_session_factory.kw["bind"].dialect.name != "mysql":
        pytest.skip("Requires isolated real MySQL fixture")
    monkeypatch.setattr(get_settings(), "workflow_execution_backend", "worker")
    run = await create_test_run(client)
    response = await client.post(f"/api/v1/runs/{run['run_id']}/agent-workflows",
                                 json=general_payload("never"))
    workflow_id = response.json()["workflow_execution_id"]
    claims = await asyncio.gather(*(
        claim_workflow(test_database_session_factory, workflow_id, owner)
        for owner in ("worker-a", "worker-b")
    ))
    assert sum(claim is not None for claim in claims) == 1


@pytest.mark.parametrize("revised", [False, True])
async def test_worker_recovers_saved_final_answer_without_model_replay(
    client, monkeypatch, test_database_session_factory, revised,
):
    """Recover a saved final node after a crash without invoking the model again."""
    from nexuspilot_api.features.agent_runtime.services.general_agent_orchestrator import (
        GeneralAgentOrchestrator,
    )
    from nexuspilot_api.features.agent_runtime.services.workflow_ownership import claim_workflow
    from nexuspilot_api.features.agent_runtime.services.workflow_worker import execute_claim

    class SimulatedProcessDeath(BaseException):
        """Bypass business exception recovery to reproduce lost execution ownership."""

    async def stop_before_completion(*args, **kwargs):
        """Interrupt after the final node commit, before completion starts."""
        raise SimulatedProcessDeath()

    monkeypatch.setattr(get_settings(), "workflow_execution_backend", "worker")
    run = await create_test_run(client)
    response = await client.post(f"/api/v1/runs/{run['run_id']}/agent-workflows",
                                 json=general_payload("always" if revised else "never"))
    workflow_id = response.json()["workflow_execution_id"]
    provider = GeneralProvider(revise=revised)
    storage = test_database_session_factory.kw["info"]["body_storage"]
    first = await claim_workflow(test_database_session_factory, workflow_id, "worker-a")
    with monkeypatch.context() as patch:
        patch.setattr(GeneralAgentOrchestrator, "_complete_workflow", stop_before_completion)
        with pytest.raises(SimulatedProcessDeath):
            await execute_claim(test_database_session_factory, storage,
                                scripted_registry(provider), fake_price_catalog(), first)
    before = (await client.get(f"/api/v1/agent-workflows/{workflow_id}/result")).json()
    assert before["status"] == "running" and before["final_output"]["final_text"]
    async with test_database_session_factory() as db:
        workflow = await db.get(LlmAgentWorkflowExecution, workflow_id)
        workflow.execution_lease_expires_at = utc_now() - timedelta(seconds=1)
        await db.commit()
    second = await claim_workflow(test_database_session_factory, workflow_id, "worker-b")
    await execute_claim(test_database_session_factory, storage,
                        scripted_registry(provider), fake_price_catalog(), second)
    after = (await client.get(f"/api/v1/agent-workflows/{workflow_id}/result")).json()
    assert after["status"] == "completed", after
    assert after["final_output"] == before["final_output"]
    assert len(provider.requests) == (4 if revised else 1)
    assert after["nodes"][-1]["input"]["workflow_round"] == (
        before["nodes"][-1]["input"]["workflow_round"])


async def test_worker_consumer_survives_job_cancel_but_respects_process_stop(monkeypatch):
    """A cancelled job must not stop the consumer; cancelling the consumer must still stop it."""
    from nexuspilot_api.commands import workflow_worker

    async def cancelled_job(*args):
        """Simulate a workflow cancelled by its independent heartbeat."""
        raise asyncio.CancelledError()

    monkeypatch.setattr(workflow_worker, "execute_claim", cancelled_job)
    await workflow_worker.execute_owned_job(None, None, None, None, None)
    started = asyncio.Event()

    async def running_job(*args):
        """Hold execution until the process task is explicitly cancelled."""
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(workflow_worker, "execute_claim", running_job)
    task = asyncio.create_task(workflow_worker.execute_owned_job(None, None, None, None, None))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
