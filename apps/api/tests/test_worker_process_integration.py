"""Opt-in OS-process/API/browser recovery against isolated local MySQL and real MinIO.

Requires NEXUSPILOT_TEST_AUTH_MYSQL_URL, NEXUSPILOT_TEST_AUTH_MINIO_* and
NEXUSPILOT_TEST_WORKER_BROWSER=1. Uses fixed model outputs, never paid models.
Starts temporary API/Web servers and Worker processes. MinIO test objects are retained;
only the pre-existing fixture's UUID test database is removed after the test.
"""

import asyncio
import json
import multiprocessing
import os
import socket
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
import uvicorn
from sqlalchemy import select
from test_agent_workflows import register_default_agent_test_model  # noqa: F401
from test_general_agent import general_model_catalog, general_payload  # noqa: F401
from worker_process_fixture import run_test_worker

from nexuspilot_api.core.config import get_settings
from nexuspilot_api.infrastructure.object_storage import get_object_storage
from nexuspilot_api.main import app
from nexuspilot_api.models import LlmAgentWorkflowExecution, LlmModelAttempt, LlmModelToolCall
from nexuspilot_api.models.base import utc_now


@pytest.mark.skipif(
    os.environ.get("NEXUSPILOT_TEST_WORKER_BROWSER") != "1",
    reason="Requires explicit local MySQL/MinIO/browser integration environment",
)
async def test_process_crash_resume_and_browser_cancel(
    resource_client,
    test_database_session_factory,
    monkeypatch,
    tmp_path,
):
    """Resume after process loss; verify browser refresh and explicit cancellation."""
    factory = test_database_session_factory
    assert factory.kw["bind"].dialect.name == "mysql"
    storage = app.dependency_overrides[get_object_storage]()
    assert storage.settings.minio_endpoint.startswith("127.0.0.1:")
    storage_config = {
        key: getattr(storage.settings, key)
        for key in (
            "minio_endpoint",
            "minio_access_key",
            "minio_secret_key",
            "minio_bucket",
            "minio_secure",
        )
    }
    database_url = factory.kw["bind"].url.render_as_string(hide_password=False)
    monkeypatch.setattr(get_settings(), "workflow_execution_backend", "worker")
    monkeypatch.setattr(get_settings(), "authentication_allowed_origins", "http://127.0.0.1:3100")
    root = Path(__file__).resolve().parents[3]
    report = root / "output/security/worker-checkpoint-joint-20261024.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, lifespan="off", log_level="error"))
    serving = asyncio.create_task(server.serve(sockets=[sock]))
    api_url = f"http://127.0.0.1:{port}"
    log = (tmp_path / "web.log").open("w")
    web = await asyncio.create_subprocess_exec(
        "npm",
        "run",
        "dev",
        "--",
        "--hostname",
        "127.0.0.1",
        "--port",
        "3100",
        cwd=root / "apps/web",
        stdout=log,
        stderr=log,
        env={
            **os.environ,
            "NEXUSPILOT_API_URL": api_url,
            "NEXUSPILOT_API_KEY": "test-api-key-long-enough",
        },
    )
    children = []
    browsers = []
    results = []
    try:
        async with httpx.AsyncClient() as http:
            async with asyncio.timeout(90):
                while True:
                    try:
                        if (await http.get("http://127.0.0.1:3100", timeout=2)).status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    await asyncio.sleep(0.5)
        owner = await resource_client.post(
            "/api/v1/users", json={"user_id": "nexuspilot-web", "display_name": "Worker test"},
            headers={"X-Internal-API-Key": "test-internal-key-long-enough"},
        )
        assert owner.status_code == 201, owner.text
        for cancel in (False, True):
            session = await resource_client.post(
                "/api/v1/sessions",
                json={"user_id": "nexuspilot-web", "title": "Worker fault verification"},
            )
            session_id = session.json()["session_id"]
            turn = await resource_client.post(
                f"/api/v1/sessions/{session_id}/turns",
                json={
                    "user_id": "nexuspilot-web",
                    "content_text": "Use the available tools then answer.",
                    "idempotency_key": f"worker-test-{cancel}",
                },
            )
            assert turn.status_code == 201, turn.text
            run_id = turn.json()["run"]["run_id"]
            accepted = await resource_client.post(
                f"/api/v1/runs/{run_id}/agent-workflows", json=general_payload("never")
            )
            assert accepted.status_code == 201, accepted.text
            workflow_id = accepted.json()["workflow_execution_id"]
            child = multiprocessing.get_context("spawn").Process(
                target=run_test_worker,
                args=(
                    database_url,
                    storage_config,
                    workflow_id,
                    None if cancel else "tool_result",
                    cancel,
                ),
            )
            children.append(child)
            child.start()
            if not cancel:
                await asyncio.to_thread(child.join, 30)
                assert child.exitcode == 86, "Worker did not reach the intended crash boundary"
                async with factory() as db:
                    row = await db.get(LlmAgentWorkflowExecution, workflow_id)
                    row.execution_lease_expires_at = utc_now() - timedelta(seconds=1)
                    await (
                        db.commit()
                    )  # Exact test row; OS exit confirmed before releasing its lease.
            screenshot = (
                root / f"output/playwright/worker-joint-{'cancel' if cancel else 'resume'}.png"
            )
            screenshot.parent.mkdir(parents=True, exist_ok=True)
            browser = await asyncio.create_subprocess_exec(
                "node",
                "e2e/worker-recovery-browser.mjs",
                cwd=root / "apps/web",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={
                    **os.environ,
                    "WORKER_TEST_WEB_URL": "http://127.0.0.1:3100",
                    "WORKER_TEST_SESSION_ID": session_id,
                    "WORKER_TEST_CANCEL": "1" if cancel else "0",
                    "WORKER_TEST_SCREENSHOT": str(screenshot),
                },
            )
            browsers.append(browser)
            line = await asyncio.wait_for(browser.stdout.readline(), 45)
            if line.strip() != b"observing":
                raise AssertionError((await browser.stderr.read()).decode()[-3000:])
            if not cancel:
                child = multiprocessing.get_context("spawn").Process(
                    target=run_test_worker, args=(database_url, storage_config, workflow_id)
                )
                children.append(child)
                child.start()
            stdout, stderr = await asyncio.wait_for(browser.communicate(), 60)
            assert browser.returncode == 0, stderr.decode()[-3000:]
            await asyncio.to_thread(child.join, 15)
            assert child.exitcode == 0
            result = (
                await resource_client.get(f"/api/v1/agent-workflows/{workflow_id}/result")
            ).json()
            async with factory() as db:
                attempts = list(
                    await db.scalars(
                        select(LlmModelAttempt).where(LlmModelAttempt.run_id == run_id)
                    )
                )
                tools = list(
                    await db.scalars(
                        select(LlmModelToolCall)
                        .join(LlmModelAttempt)
                        .where(LlmModelAttempt.run_id == run_id)
                    )
                )
            assert result["status"] == ("cancelled" if cancel else "completed")
            assert len(attempts) == (1 if cancel else 2)
            assert len(tools) == (0 if cancel else 1)
            results.append(
                {
                    "run_id": run_id,
                    "workflow_id": workflow_id,
                    "status": result["status"],
                    "model_attempt_count": len(attempts),
                    "tool_call_count": len(tools),
                    "final_text_present": bool(result.get("final_output")),
                    "browser_verified": True,
                }
            )
        report.write_text(
            json.dumps(
                {
                    "provider": "fixed local test provider; no paid calls",
                    "mysql": True,
                    "minio": True,
                    "scenarios": results,
                },
                indent=2,
            )
        )
    finally:
        for browser in browsers:
            if browser.returncode is None:
                browser.terminate()
                await browser.wait()
        for child in children:
            if child.is_alive():
                child.terminate()
                await asyncio.to_thread(child.join, 5)
        web.terminate()
        await web.wait()
        log.close()
        server.should_exit = True
        await serving
