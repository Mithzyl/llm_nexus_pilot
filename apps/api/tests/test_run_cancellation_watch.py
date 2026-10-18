"""Verify durable cancellation interrupts an otherwise unbounded provider wait."""

import asyncio

import pytest
from sqlalchemy import update
from test_api import create_test_run

from nexuspilot_api.models import LlmRun
from nexuspilot_api.models.enums import RunStatus
from nexuspilot_api.services.run_cancellation_watch import watch_run_cancellation


async def test_durable_cancel_interrupts_wait_and_closes_operation(
    client, test_database_session_factory,
):
    """A separate committed cancellation must interrupt a waiting provider without a deadline."""
    run = await create_test_run(client)
    entered = asyncio.Event()
    closed = asyncio.Event()

    async def operation():
        """Represent an idle provider and expose its cleanup, without making a paid call."""
        async with test_database_session_factory() as db:
            async with watch_run_cancellation(db, run["run_id"], poll_seconds=0.01):
                try:
                    entered.set()
                    await asyncio.Event().wait()
                finally:
                    closed.set()

    task = asyncio.create_task(operation())
    await entered.wait()
    async with test_database_session_factory() as db:
        await db.execute(update(LlmRun).where(LlmRun.run_id == run["run_id"])
                         .values(status=RunStatus.CANCELLED))
        await db.commit()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=1)
    assert closed.is_set()


async def test_completed_operation_releases_cancellation_watcher(
    client, test_database_session_factory,
):
    """Normal completion leaves no polling task and does not cancel the caller later."""
    run = await create_test_run(client)
    async with test_database_session_factory() as db:
        async with watch_run_cancellation(db, run["run_id"], poll_seconds=0.01):
            await asyncio.sleep(0.02)
    assert not any(task.get_name() == f"cancel-watch:{run['run_id']}"
                   for task in asyncio.all_tasks())


@pytest.mark.parametrize("stream", [False, True])
async def test_cancel_endpoint_interrupts_idle_response_provider(client, stream):
    """Exercise the actual response service and cancel endpoint with an idle scripted provider."""
    from test_agent_workflows import scripted_registry
    from test_conversation_file_context import prepare_conversation_file
    from test_response_file_tools import QuickFileProvider

    from nexuspilot_api.core.dependencies import get_provider_registry
    from nexuspilot_api.main import app

    entered, closed = asyncio.Event(), asyncio.Event()

    class IdleProvider(QuickFileProvider):
        """Keep the provider connection idle until durable cancellation propagates."""

        async def generate(self, request):
            """Record dispatch and verify cancellation reaches provider cleanup."""
            self.requests.append(request)
            try:
                entered.set()
                await asyncio.Event().wait()
            finally:
                closed.set()

    _, _, _, turn = await prepare_conversation_file(client)
    provider = IdleProvider()
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)
    task = asyncio.create_task(client.post("/api/v1/responses", json={
        "run_id": turn["run"]["run_id"],
        "current_user_message_id": turn["message"]["message_id"],
        "provider": "openai", "model": "test-model", "input": "Read the file",
        "stream": stream, "timeout_seconds": None,
    }))
    try:
        await asyncio.wait_for(entered.wait(), timeout=2)
        response = await client.post(f"/api/v1/runs/{turn['run']['run_id']}/cancel")
        assert response.status_code == 200
        await asyncio.wait_for(closed.wait(), timeout=2)
        await asyncio.gather(task, return_exceptions=True)
        assert len(provider.requests) == 1
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
