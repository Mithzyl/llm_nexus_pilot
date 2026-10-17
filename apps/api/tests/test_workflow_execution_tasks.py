"""Offline lifecycle tests: observers do not own execution; application shutdown does."""

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import Response
from test_agent_workflows import register_default_agent_test_model  # noqa: F401

from nexuspilot_api.features.agent_runtime.routers.agent_workflows import post_agent_workflow
from nexuspilot_api.features.agent_runtime.services.workflow_execution_tasks import (
    WorkflowExecutionTasks,
)


@pytest.mark.parametrize("action", ["finish", "cancel", "shutdown"])
async def test_disconnected_workflow_persists_result_or_explicit_cancellation(
    client,
    test_database_session_factory,
    action,
):
    """Exercise real SQL execution after ASGI disconnect and enforce explicit cancellation."""
    from test_agent_workflows import ReleasableWorkerProvider, scripted_registry, workflow_payload
    from test_api import create_test_run

    from nexuspilot_api.core.dependencies import get_price_catalog, get_provider_registry
    from nexuspilot_api.features.agent_runtime.schemas.agent_workflows import AgentWorkflowCreate
    from nexuspilot_api.features.agent_runtime.services.workflow_execution_service import (
        AgentWorkflowExecutionService,
    )
    from nexuspilot_api.features.agent_runtime.services.workflow_execution_tasks import (
        workflow_execution_tasks,
    )
    from nexuspilot_api.infrastructure.object_storage import get_object_storage
    from nexuspilot_api.main import app

    run = await create_test_run(client)
    provider = ReleasableWorkerProvider()
    registry = scripted_registry(provider)
    app.dependency_overrides[get_provider_registry] = lambda: registry
    payload = AgentWorkflowCreate.model_validate(workflow_payload(stream=True))
    async with test_database_session_factory() as db:
        service = AgentWorkflowExecutionService(
            registry=registry,
            prices=app.dependency_overrides[get_price_catalog](),
            db_session=db,
            db_session_factory=test_database_session_factory,
            storage=app.dependency_overrides[get_object_storage](),
        )
        response = await post_agent_workflow(run["run_id"], payload, Response(), service)
        await asyncio.wait_for(provider.worker_request_started.wait(), 3)
        summary = (await client.get(f"/api/v1/runs/{run['run_id']}/agent-workflow")).json()
        workflow_id = summary["workflow_execution_id"]
        task = workflow_execution_tasks.active[workflow_id]

        async def receive():
            """Send a real ASGI disconnect to the StreamingResponse listener."""
            return {"type": "http.disconnect"}

        async def send(message):
            """Accept response frames without an actual browser or network."""
            pass

        try:
            await response({"type": "http", "asgi": {"spec_version": "2.3"}}, receive, send)
            assert not task.done()
            if action == "cancel":
                cancelled = await client.post(f"/api/v1/agent-workflows/{workflow_id}/cancel")
                assert cancelled.status_code == 200
            elif action == "shutdown":
                await workflow_execution_tasks.shutdown(timeout_seconds=0)
        finally:
            provider.release_worker_request.set()
        if action != "shutdown":
            await asyncio.wait_for(asyncio.shield(task), 5)
    result = (await client.get(f"/api/v1/agent-workflows/{workflow_id}/result")).json()
    expected = {"finish": "completed", "cancel": "cancelled", "shutdown": "outcome_unknown"}[action]
    assert result["status"] == expected
    if action == "shutdown":
        assert result["error"]["error_code"] == "agent_provider_outcome_unknown"
        assert result["error"]["outcome_is_known"] is False
    count = len(provider.requests)
    replay = await client.post(
        f"/api/v1/runs/{run['run_id']}/agent-workflows", json=payload.model_dump(mode="json")
    )
    assert replay.status_code == 200
    assert len(provider.requests) == count
    if action == "finish":
        assert result["final_output"]["final_text"]


async def test_closing_sse_observer_does_not_cancel_execution():
    """Disconnect after one committed event and require the original execution to finish."""
    started, release, finished = asyncio.Event(), asyncio.Event(), asyncio.Event()

    class Service:
        """Delay a single execution while the HTTP body observer is closed."""

        async def prepare_workflow(self, *args, **kwargs):
            """Return a newly committed workflow without a database dependency."""
            return SimpleNamespace(workflow_execution_id="disconnect-fixture"), False

        async def execute_prepared_workflow_in_new_session(self, *args, event_sink, **kwargs):
            """Publish progress, wait for test release, and mark completion."""
            started.set()
            await event_sink(SimpleNamespace(to_sse=lambda: "data: started\n\n"))
            await release.wait()
            finished.set()

    response = await post_agent_workflow("run", SimpleNamespace(stream=True), Response(), Service())
    iterator = response.body_iterator
    try:
        assert await anext(iterator) == "data: started\n\n"
        await iterator.aclose()
        release.set()
        await asyncio.wait_for(finished.wait(), 1)
    finally:
        release.set()


async def test_execution_starts_even_without_consuming_response_body():
    """A response disconnect before the first byte must not leave committed work pending."""
    finished = asyncio.Event()

    class Service:
        """Record whether execution starts independently of iterating the response."""

        async def prepare_workflow(self, *args, **kwargs):
            """Return a distinct newly committed workflow."""
            return SimpleNamespace(workflow_execution_id="before-body-fixture"), False

        async def execute_prepared_workflow_in_new_session(self, *args, **kwargs):
            """Finish immediately to detect lazy execution tied to body iteration."""
            finished.set()

    await post_agent_workflow("run", SimpleNamespace(stream=True), Response(), Service())
    await asyncio.wait_for(finished.wait(), 1)


async def test_shutdown_waits_for_completion_then_cancels_unfinished_tasks():
    """Shutdown releases execution resources only after waiting or propagating cancellation."""
    tasks = WorkflowExecutionTasks()
    cancelled = asyncio.Event()

    async def unfinished():
        """Expose cancellation cleanup so shutdown can prove it awaited it."""
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    tasks.start("shutdown", unfinished())
    await asyncio.sleep(0)
    await tasks.shutdown(timeout_seconds=0)
    assert cancelled.is_set()
    assert not tasks.active
