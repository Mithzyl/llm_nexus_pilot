"""Opt-in real Worker crash/competition tests with isolated MySQL, MinIO and caller HTTP.

Set NEXUSPILOT_TEST_CALLER_WORKER=1 and existing AUTH_MYSQL_URL/MINIO fixture variables.
Keep test databases with NEXUSPILOT_TEST_AUTH_KEEP_DATABASE=true. No Assistant or paid model.
"""

import asyncio
import json
import multiprocessing
import os
from pathlib import Path

import pytest
from application_worker_process_fixture import run_caller_worker
from sqlalchemy import func, select
from test_agent_workflows import register_default_agent_test_model  # noqa: F401
from test_application_body_processing import HEAD, INPUT
from test_application_body_processing import body_peer as caller_body_peer
from test_application_tool_loop import app_headers
from test_general_agent import general_model_catalog, general_payload  # noqa: F401

from nexuspilot_api.core.config import get_settings
from nexuspilot_api.infrastructure.object_storage import get_object_storage
from nexuspilot_api.main import app
from nexuspilot_api.models import (
    ApplicationResponseExecution,
    LlmAgentWorkflowExecution,
    LlmModelAttempt,
)

pytestmark = pytest.mark.skipif(
    os.environ.get("NEXUSPILOT_TEST_CALLER_WORKER") != "1",
    reason="Requires opt-in isolated real MySQL/MinIO Worker test",
)

body_peer = caller_body_peer


async def prepare_job(client, factory, monkeypatch):
    """Create a pending App workflow and return private child-process settings."""
    assert factory.kw["bind"].dialect.name == "mysql"
    monkeypatch.setattr(get_settings(), "workflow_execution_backend", "worker")
    storage = app.dependency_overrides[get_object_storage]()
    assert storage.settings.minio_endpoint.startswith("127.0.0.1:")
    config = {
        key: getattr(storage.settings, key)
        for key in (
            "minio_endpoint",
            "minio_access_key",
            "minio_secret_key",
            "minio_bucket",
            "minio_secure",
        )
    }
    headers = app_headers("personal_assistant", "body-user")
    run = (
        await client.post("/api/v1/app/runs", headers=headers, json={"user_request": INPUT})
    ).json()
    response = await client.post(
        f"/api/v1/app/runs/{run['run_id']}/agent-workflows",
        headers=headers,
        json=general_payload(
            "never",
            application_response_mode="caller_processed",
            application_protocol_instructions=HEAD,
        ),
    )
    assert response.status_code == 201 and response.json()["status"] == "pending", response.text
    identity = response.json()["workflow_execution_id"]
    args = (
        factory.kw["bind"].url.render_as_string(hide_password=False),
        config,
        get_settings().app_tools_json.get_secret_value(),
        identity,
    )
    return run["run_id"], identity, args, headers


async def join_worker(process):
    """Bound observation of the existing child without starting a replacement on timeout."""
    await asyncio.to_thread(process.join, 40)
    assert not process.is_alive(), "Worker still alive; no replacement was started"


async def cleanup_workers(children):
    """Stop only this test's spawned processes after failure; retain databases and objects."""
    for child in children:
        if child.is_alive():
            child.terminate()
            await asyncio.to_thread(child.join, 5)


def record_evidence(item):
    """Append only synthetic identifiers and counts to the existing security evidence directory."""
    item['batch'] = os.environ.get('NEXUSPILOT_TEST_EVIDENCE_BATCH', 'initial')
    path = (
        Path(__file__).resolve().parents[3]
        / "output/security/application-body-worker-20261105.json"
    )
    data = json.loads(path.read_text()) if path.exists() else {"provider": "fixed", "scenarios": []}
    data["scenarios"].append(item)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")


@pytest.mark.parametrize(
    "phase",
    [
        "prepared",
        "dispatching",
        "platform_returned",
        "result_saved",
        "messages_before_checkpoint",
        "messages_checkpoint",
    ],
)
async def test_real_worker_crash_boundary(
    resource_client, test_database_session_factory, monkeypatch, body_peer, phase
):
    """Resume known results without repeating calls; retain ambiguous effects as unknown."""
    factory = test_database_session_factory
    run_id, workflow_id, args, headers = await prepare_job(resource_client, factory, monkeypatch)
    context = multiprocessing.get_context("spawn")
    first = context.Process(target=run_caller_worker, args=(*args, phase))
    children = [first]
    try:
        first.start()
        await join_worker(first)
        assert first.exitcode == 86, f"Crash boundary not reached: {phase}, exit {first.exitcode}"
        # Observe real lease expiry only after the child is confirmed dead.
        async with asyncio.timeout(15):
            while True:
                async with factory() as db:
                    live = await db.scalar(
                        select(LlmAgentWorkflowExecution.workflow_execution_id).where(
                            LlmAgentWorkflowExecution.workflow_execution_id == workflow_id,
                            LlmAgentWorkflowExecution.execution_lease_expires_at
                            > func.current_timestamp(),
                        )
                    )
                if not live:
                    break
                await asyncio.sleep(0.2)
        second = context.Process(target=run_caller_worker, args=args)
        children.append(second)
        second.start()
        await join_worker(second)
        assert second.exitcode == 0
        result = (
            await resource_client.get(
                f"/api/v1/app/agent-workflows/{workflow_id}/result", headers=headers
            )
        ).json()
        unknown = phase in {"dispatching", "platform_returned"}
        assert result["status"] == ("outcome_unknown" if unknown else "completed"), result
        async with factory() as db:
            attempts = await db.scalar(
                select(func.count())
                .select_from(LlmModelAttempt)
                .where(LlmModelAttempt.run_id == run_id)
            )
            records = await db.scalar(
                select(func.count())
                .select_from(ApplicationResponseExecution)
                .where(ApplicationResponseExecution.run_id == run_id)
            )
        assert attempts == (1 if unknown else 2)
        assert len(body_peer) == (0 if phase == "dispatching" else 1 if unknown else 2)
        assert records == (1 if unknown else 2)
        record_evidence(
            {
                "phase": phase,
                "run_id": run_id,
                "workflow_id": workflow_id,
                "status": result["status"],
                "attempts": attempts,
                "http_calls": len(body_peer),
                "worker_exit": first.exitcode,
                "recovery_exit": second.exitcode,
                "database": factory.kw["bind"].url.database,
                "passed": True,
            }
        )
    finally:
        await cleanup_workers(children)


async def test_two_real_workers_compete(
    resource_client, test_database_session_factory, monkeypatch, body_peer
):
    """Release two real Workers together; exactly one claim produces the two expected HTTP calls."""
    factory = test_database_session_factory
    run_id, workflow_id, args, headers = await prepare_job(resource_client, factory, monkeypatch)
    context = multiprocessing.get_context("spawn")
    ready, claimed, start = context.Queue(), context.Queue(), context.Event()
    children = [
        context.Process(target=run_caller_worker, args=(*args, None, ready, start, claimed))
        for _ in range(2)
    ]
    try:
        for child in children:
            child.start()
        for _ in children:
            await asyncio.to_thread(ready.get, True, 30)
        start.set()
        for child in children:
            await join_worker(child)
            assert child.exitcode == 0
        claims = [claimed.get(timeout=3) for _ in children]
        assert sum(item["claimed"] for item in claims) == 1
        result = (
            await resource_client.get(
                f"/api/v1/app/agent-workflows/{workflow_id}/result", headers=headers
            )
        ).json()
        assert result["status"] == "completed", result
        assert len(body_peer) == 2
        async with factory() as db:
            attempts = await db.scalar(
                select(func.count())
                .select_from(LlmModelAttempt)
                .where(LlmModelAttempt.run_id == run_id)
            )
        assert attempts == 2
        record_evidence(
            {
                "phase": "two_workers",
                "run_id": run_id,
                "workflow_id": workflow_id,
                "status": result["status"],
                "attempts": attempts,
                "http_calls": len(body_peer),
                "claimed_workers": 1,
                "database": factory.kw["bind"].url.database,
                "passed": True,
            }
        )
    finally:
        start.set()
        await cleanup_workers(children)


async def test_changed_connection_stops_worker_before_first_model(
    resource_client, test_database_session_factory, monkeypatch, body_peer
):
    """A queued job must not adopt a changed endpoint or spend a model call before rejecting it."""
    factory = test_database_session_factory
    run_id, workflow_id, args, headers = await prepare_job(resource_client, factory, monkeypatch)
    changed = json.loads(args[2])
    changed["personal_assistant"]["response_processing_path"] = "/changed-after-creation"
    child = multiprocessing.get_context("spawn").Process(
        target=run_caller_worker, args=(args[0], args[1], json.dumps(changed), args[3])
    )
    try:
        child.start()
        await join_worker(child)
        assert child.exitcode == 0
        result = (
            await resource_client.get(
                f"/api/v1/app/agent-workflows/{workflow_id}/result", headers=headers
            )
        ).json()
        assert result["status"] == "failed", result
        async with factory() as db:
            attempts = await db.scalar(
                select(func.count())
                .select_from(LlmModelAttempt)
                .where(LlmModelAttempt.run_id == run_id)
            )
        assert attempts == 0 and not body_peer
        record_evidence(
            {
                "phase": "connection_changed_before_first_call",
                "run_id": run_id,
                "workflow_id": workflow_id,
                "status": result["status"],
                "attempts": attempts,
                "http_calls": len(body_peer),
                "database": factory.kw["bind"].url.database,
                "passed": True,
            }
        )
    finally:
        await cleanup_workers([child])
