"""Exercise Python authorization and persisted outputs with synthetic models and storage."""

import asyncio
import base64
import json
import os
import tempfile
import threading
from pathlib import Path

import pytest
from nexuspilot_models.contracts import ModelResponse, ToolCall
from sqlalchemy import select
from test_agent_workflows import register_default_agent_test_model, scripted_registry  # noqa: F401
from test_api import create_test_run
from test_general_agent import GeneralProvider, general_model_catalog, general_payload  # noqa: F401
from test_python_runtime import python_server  # noqa: F401

from nexuspilot_api.core.config import get_settings
from nexuspilot_api.core.dependencies import get_provider_registry
from nexuspilot_api.main import app
from nexuspilot_api.models import LlmModelToolCall, LlmRunArtifact


@pytest.fixture
async def real_python_backend(client, monkeypatch, python_server, tmp_path):  # noqa: F811
    """Use real Docker and MinIO for synthetic artifacts, leaving conversation fixtures isolated."""
    from nexuspilot_api.infrastructure.object_storage import ObjectStorage, get_object_storage

    actual = ObjectStorage()
    fixture = app.dependency_overrides[get_object_storage]()
    put, open_object = fixture.put_bytes, fixture.open_object
    created = []

    async def put_artifact(name, content, content_type):
        """Persist only the test artifact to MinIO and retain its exact cleanup URI."""
        if name.endswith("/result.csv"):
            stored = await actual.put_bytes(name, content, content_type)
            created.append(stored.uri)
            return stored
        return await put(name, content, content_type)

    async def open_artifact(uri, **kwargs):
        """Read the test's own real objects; ordinary fixture bodies stay in memory."""
        if uri in created:
            return await actual.open_object(uri, **kwargs)
        return await open_object(uri, **kwargs)

    monkeypatch.setattr(fixture, "put_bytes", put_artifact)
    monkeypatch.setattr(fixture, "open_object", open_artifact)
    runtime = python_server.PythonRuntime(
        tmp_path / "runtime", python_server.resolve_image("nexuspilot-task-runtime:local"),
    )
    with tempfile.TemporaryDirectory(dir="/tmp") as directory:
        path = Path(directory) / "p.sock"
        server = python_server.Server(path, runtime)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield str(path)
        finally:
            await asyncio.to_thread(server.shutdown)
            await asyncio.to_thread(server.server_close)
            thread.join(timeout=2)
            for uri in created:
                await actual.delete_object(uri)


@pytest.mark.parametrize("case", [
    "success", "denied", "shell", "failed", "revoked", "foreign_input", "identity", "storage",
    "invalid_result",
    "foreign_artifact",
])
async def test_python_tool_authorization_and_artifact(
    client, monkeypatch, test_database_session_factory, case, real_path=None,
):
    """Only an enabled owner executes Python; outputs and failed exits retain their real status."""
    from nexuspilot_api.services import python_tool_service as service

    run = await create_test_run(client)
    foreign_artifact = None
    if case == "foreign_artifact":
        other = await create_test_run(client)
        async with test_database_session_factory() as db:
            artifact = LlmRunArtifact(
                run_id=other["run_id"], artifact_type="fixture", filename="private.csv",
                mime_type="text/csv", size_bytes=1, content_hash="0" * 64,
                storage_uri="memory://not-granted",
            )
            db.add(artifact)
            await db.commit()
            foreign_artifact = artifact.artifact_id
    monkeypatch.setattr(
        get_settings(), "python_runtime_socket", real_path or "/tmp/test-python.sock",
    )
    monkeypatch.setattr(
        get_settings(), "python_execution_user_ids", [] if case == "denied" else [run["user_id"]],
    )
    calls = []
    transport = service.execute_python_request

    async def execute(socket_path, request):
        """Return a synthetic container result; assert identity was derived by the service."""
        calls.append(request)
        assert request["scope"]["user_id"] == run["user_id"]
        assert request["scope"]["run_id"] == run["run_id"]
        assert request["command"]["executable"] == "python"
        if real_path:
            return await transport(socket_path, request)
        return {
            "status": "failed" if case == "failed" else "completed",
            "exit_code": 1 if case == "failed" else 0,
            "stdout": "6", "stderr": "example failure" if case == "failed" else "",
            "stdout_truncated": False, "stderr_truncated": False,
            "container_removed": case != "invalid_result", "image_id": "sha256:" + "a" * 64,
            "files": {"result.csv": base64.b64encode(b"total\n6\n").decode()},
        }

    monkeypatch.setattr(service, "execute_python_request", execute)
    if case == "storage":
        from nexuspilot_api.infrastructure.object_storage import (
            ObjectStorageError,
            get_object_storage,
        )

        storage = app.dependency_overrides[get_object_storage]()
        put = storage.put_bytes

        async def fail_artifact(name, content, content_type):
            """Fail only result-file persistence, retaining ordinary conversation body storage."""
            if name.endswith("/result.csv"):
                raise ObjectStorageError("Synthetic storage outage")
            return await put(name, content, content_type)

        monkeypatch.setattr(storage, "put_bytes", fail_artifact)

    class Provider(GeneralProvider):
        """Select the deferred Python tool and inspect the durable tool message."""

        async def generate(self, request):
            """Ask once for the definition, once for execution, then consume its result."""
            previous = [m for m in request.messages if m.role.value == "tool"]
            if not previous:
                call = ToolCall(id="load", name="describe_tools", arguments={
                    "name": "run_command", "version": "1.0.0",
                })
            elif len(previous) == 1:
                if case == "revoked":
                    monkeypatch.setattr(get_settings(), "python_execution_user_ids", [])
                call = ToolCall(id="execute", name="run_command", arguments={
                    "executable": "shell" if case == "shell" else "python",
                    "stdin": "from pathlib import Path\nprint(sum([1,2,3]))\n"
                    "Path('result.csv').write_text('total\\n6\\n')",
                    "output_files": ["result.csv"],
                    **({"file_ids": ["not-granted"]} if case == "foreign_input" else {}),
                    **({"user_id": "someone-else"} if case == "identity" else {}),
                    **({"artifact_ids": [foreign_artifact]} if foreign_artifact else {}),
                })
            else:
                result = json.loads(previous[-1].content)
                expected = {
                    "success": "completed", "denied": "permission_denied",
                    "revoked": "permission_denied", "shell": "failed", "failed": "failed",
                    "foreign_input": "permission_denied", "identity": "failed", "storage": "failed",
                    "invalid_result": "failed",
                    "foreign_artifact": "permission_denied",
                }[case]
                assert result["status"] == expected, result
                if calls and case != "invalid_result":
                    assert result["result"]["stdout"].strip() == "6"
                    if case == "storage":
                        assert result["error_code"] == "python_artifact_save_failed"
                        assert not result["result"]["artifacts"]
                    else:
                        assert result["result"]["artifacts"][0]["artifact_id"]
                    assert "storage_uri" not in str(result)
                return ModelResponse(
                    text="Test finished", finish_reason="stop", latency_ms=1, raw_response={},
                )
            return ModelResponse(
                tool_calls=[call], finish_reason="tool_calls", latency_ms=1, raw_response={},
            )

    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(Provider())
    response = await client.post(
        f"/api/v1/runs/{run['run_id']}/agent-workflows", json=general_payload("never"),
    )
    assert response.json()["status"] == (
        "failed" if case == "revoked" else "completed"
    ), response.text
    assert len(calls) == (1 if case in {"success", "failed", "storage", "invalid_result"} else 0)
    async with test_database_session_factory() as db:
        records = list(await db.scalars(select(LlmModelToolCall).where(
            LlmModelToolCall.tool_name == "run_command",
        )))
        assert len(records) == 1
        assert records[0].body_ref_json
        artifacts = list(await db.scalars(select(LlmRunArtifact).where(
            LlmRunArtifact.run_id == run["run_id"],
        )))
        assert len(artifacts) == (0 if case in {"storage", "invalid_result"} else len(calls))
        if artifacts:
            artifact = artifacts[0]
            assert artifact.task_id == calls[0]["scope"]["task_id"]
            downloaded = await client.get(f"/api/v1/artifacts/{artifact.artifact_id}/content")
            assert downloaded.content == b"total\n6\n"


@pytest.mark.skipif(
    os.environ.get("NEXUSPILOT_TEST_TASK_RUNTIME") != "1", reason="Requires Docker and local MinIO",
)
async def test_real_python_agent_and_minio(
    client, monkeypatch, test_database_session_factory, real_python_backend,
):
    """Run the Agent tool path through Docker and download its MinIO result without an LLM."""
    await test_python_tool_authorization_and_artifact(
        client, monkeypatch, test_database_session_factory, "success", real_python_backend,
    )


async def test_python_transport_cancel_waits_for_stop():
    """A cancelled caller signals the executor and waits for its confirmed cleanup response."""
    from nexuspilot_api.services.python_tool_service import execute_python_request

    started, stopped = asyncio.Event(), asyncio.Event()

    async def serve(reader, writer):
        """Receive a cancel frame and acknowledge container removal before closing."""
        await reader.readline()
        started.set()
        assert json.loads(await reader.readline()) == {"cancel": True}
        stopped.set()
        writer.write(b'{"status":"cancelled","container_removed":true}\n')
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    with tempfile.TemporaryDirectory(dir="/tmp") as directory:
        path = directory + "/p.sock"
        server = await asyncio.start_unix_server(serve, path)
        async with server:
            task = asyncio.create_task(execute_python_request(path, {"test": True}))
            await asyncio.wait_for(started.wait(), 2)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 2)
            assert stopped.is_set()


async def test_python_workflow_cancel_propagates(client, monkeypatch, real_path=None):
    """Explicit workflow cancellation interrupts Python and prevents another model call."""
    from nexuspilot_api.services import python_tool_service as service

    run = await create_test_run(client)
    monkeypatch.setattr(
        get_settings(), "python_runtime_socket", real_path or "/tmp/test-python.sock",
    )
    monkeypatch.setattr(get_settings(), "python_execution_user_ids", [run["user_id"]])
    started, stopped = asyncio.Event(), asyncio.Event()

    async def execute(*args):
        """Block until the existing workflow controller cancels the in-flight tool."""
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    containers = []
    if real_path:
        from task_runtime import TaskExecutor, docker

        original = TaskExecutor.create_container

        def observe_container(executor, name, workspace):
            """Capture this test's container to wait for actual Python execution."""
            original(executor, name, workspace)
            containers.append(name)

        monkeypatch.setattr(TaskExecutor, "create_container", observe_container)
    else:
        monkeypatch.setattr(service, "execute_python_request", execute)

    class Provider(GeneralProvider):
        """Request Python once and reject any generation after cancellation."""

        async def generate(self, request):
            """Describe then invoke; no final response should be requested after stopping."""
            self.requests.append(request)
            assert len(self.requests) <= 2
            call = (
                ToolCall(id="load", name="describe_tools", arguments={
                    "name": "run_command", "version": "1.0.0",
                }) if len(self.requests) == 1 else ToolCall(
                    id="execute", name="run_command", arguments={"stdin": "while True: pass"},
                )
            )
            return ModelResponse(
                tool_calls=[call], finish_reason="tool_calls", latency_ms=1, raw_response={},
            )

    provider = Provider()
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)
    pending = asyncio.create_task(client.post(
        f"/api/v1/runs/{run['run_id']}/agent-workflows",
        json=general_payload("never", stream=True),
    ))
    try:
        if real_path:
            async with asyncio.timeout(20):
                while True:
                    if containers:
                        top = await asyncio.to_thread(
                            docker, ["top", containers[-1], "-eo", "pid,args"], check=False,
                        )
                        if b"python -I" in top.stdout:
                            break
                    await asyncio.sleep(0.05)
        else:
            await asyncio.wait_for(started.wait(), 5)
        summary = (await client.get(f"/api/v1/runs/{run['run_id']}/agent-workflow")).json()
        workflow = summary["workflow_execution_id"]
        assert (await client.post(f"/api/v1/agent-workflows/{workflow}/cancel")).status_code == 200
        await asyncio.wait_for(pending, 30 if real_path else 5)
        if real_path:
            remaining = await asyncio.to_thread(
                docker,
                ["ps", "-a", "--filter", f"name={containers[-1]}", "--format", "{{.Names}}"],
            )
            assert not remaining.stdout.strip()
        else:
            assert stopped.is_set()
        result = (await client.get(f"/api/v1/agent-workflows/{workflow}/result")).json()
        assert result["status"] == "cancelled" and result["final_output"] is None
        assert len(provider.requests) == 2
    finally:
        if not pending.done():
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)


@pytest.mark.skipif(
    os.environ.get("NEXUSPILOT_TEST_TASK_RUNTIME") != "1", reason="Requires local Docker and MinIO",
)
async def test_real_python_workflow_cancel(client, monkeypatch, real_python_backend):
    """Cancel through the workflow API and confirm the running real container is removed."""
    await test_python_workflow_cancel_propagates(client, monkeypatch, real_python_backend)
