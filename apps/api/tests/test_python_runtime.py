"""Verify the Python socket boundary; opt-in Docker tests use synthetic inputs only."""

import asyncio
import base64
import importlib
import os
import tempfile
import threading
import uuid
from pathlib import Path

import pytest


@pytest.fixture
def python_server(monkeypatch):
    """Load the administrator service without giving the API process a Docker implementation."""
    directory = Path(__file__).resolve().parents[3] / "deployments/task-runtime"
    monkeypatch.syspath_prepend(str(directory))
    return importlib.import_module("serve_python")


def request_for(source):
    """Create one trusted synthetic scope and command with no host paths or credentials."""
    return {
        "scope": {"user_id": "test-owner", "run_id": "test-run", "task_id": "test-task"},
        "call_id": str(uuid.uuid4()), "command": {"executable": "python", "stdin": source},
        "inputs": {"data.csv": base64.b64encode(b"value\n1\n2\n3\n").decode()},
        "output_files": ["result.csv"],
    }


@pytest.mark.parametrize("field,value", [
    ("command", {"executable": "shell", "stdin": "echo no"}),
    ("command", {"executable": "python", "stdin": "pass", "arguments": ["/host/file"]}),
    ("output_files", ["../outside"]),
    ("inputs", {"/absolute": "YQ=="}),
])
def test_python_server_rejects_non_python_protocol(python_server, field, value):
    """Reject Shell, host scripts and invalid paths before contacting the existing executor."""
    payload = request_for("print(1)")
    payload[field] = value
    with pytest.raises(ValueError):
        python_server.validate_request(payload)


async def test_python_server_socket_cancel_frame(python_server):
    """Exercise the real threaded socket server's stop signaling without requiring Docker."""
    from nexuspilot_api.services.python_tool_service import execute_python_request

    started, stopped = threading.Event(), threading.Event()

    class Runtime:
        """Stand in for Docker while preserving the service's actual connection handling."""

        def execute(self, request, cancel):
            """Observe the trusted envelope and wait for an actual socket cancellation frame."""
            assert request["scope"]["user_id"] == "test-owner"
            started.set()
            assert cancel.wait(5)
            stopped.set()
            return {"status": "cancelled", "container_removed": True}

    with tempfile.TemporaryDirectory(dir="/tmp") as directory:
        path = Path(directory) / "p.sock"
        server = python_server.Server(path, Runtime())
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        pending = asyncio.create_task(execute_python_request(str(path), request_for("pass")))
        try:
            assert await asyncio.to_thread(started.wait, 3)
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(pending, 3)
            assert stopped.is_set()
        finally:
            if not pending.done():
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
            await asyncio.to_thread(server.shutdown)
            await asyncio.to_thread(server.server_close)
            thread.join(timeout=2)


@pytest.mark.skipif(
    os.environ.get("NEXUSPILOT_TEST_TASK_RUNTIME") != "1", reason="Requires local Docker image",
)
async def test_real_python_socket_execution_and_stop(python_server, tmp_path):
    """Use actual Docker for CSV calculation, nonzero exit and confirmed in-flight cancellation."""
    from nexuspilot_api.services.python_tool_service import execute_python_request

    runtime = python_server.PythonRuntime(
        tmp_path / "workspaces", python_server.resolve_image("nexuspilot-task-runtime:local"),
    )
    with tempfile.TemporaryDirectory(dir="/tmp") as directory:
        path = Path(directory) / "p.sock"
        server = python_server.Server(path, runtime)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        pending = None
        try:
            payload = request_for(
                "import csv\nfrom pathlib import Path\n"
                "rows=csv.DictReader(open('/input/data.csv'))\n"
                "total=sum(int(r['value']) for r in rows)\n"
                "Path('result.csv').write_text(f'total\\n{total}\\n')\nprint(total)"
            )
            result = await execute_python_request(str(path), payload)
            assert result["status"] == "completed"
            assert result["stdout"].strip() == "6"
            assert base64.b64decode(result["files"]["result.csv"]) == b"total\n6\n"
            assert await execute_python_request(str(path), payload) == result
            continuation = request_for(
                "from pathlib import Path\n"
                "assert Path('result.csv').read_text() == 'total\\n6\\n'\n"
                "Path('result.csv').write_text('total\\n12\\n')"
            )
            continuation["inputs"] = {}
            continued = await execute_python_request(str(path), continuation)
            assert base64.b64decode(continued["files"]["result.csv"]) == b"total\n12\n"
            # An older call must export its own immutable generation, not the current task files.
            assert await execute_python_request(str(path), payload) == result
            failed = await execute_python_request(
                str(path), request_for("raise ValueError('sample')"),
            )
            assert failed["status"] == "failed" and failed["exit_code"] != 0
            assert "ValueError" in failed["stderr"]

            started = threading.Event()
            containers = []
            original = runtime.executor.create_container

            def observe_start(name, workspace):
                """Observe container creation before requesting stop, without timing guesses."""
                original(name, workspace)
                containers.append(name)
                started.set()

            runtime.executor.create_container = observe_start
            pending = asyncio.create_task(execute_python_request(
                str(path), request_for("import time\ntime.sleep(120)"),
            ))
            assert await asyncio.to_thread(started.wait, 15)
            from task_runtime import docker

            for _ in range(100):
                top = await asyncio.to_thread(
                    docker, ["top", containers[-1], "-eo", "pid,args"], check=False,
                )
                if b"python -I" in top.stdout:
                    break
                await asyncio.sleep(0.05)
            else:
                pytest.fail(f"Python process did not start: {top.stdout!r}; {top.stderr!r}")
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(pending, 30)
            after_stop = request_for(
                "from pathlib import Path\nprint(Path('result.csv').read_text())"
            )
            final = await execute_python_request(str(path), after_stop)
            assert "12" in final["stdout"]
            # Every completed call, including cancellation, must be ready with no container.
            for workspace in runtime.store.root.iterdir():
                if workspace.is_dir():
                    import json

                    state = json.loads((workspace / "state.json").read_text())
                    assert state["state"] == "ready" and state["container"] is None
        finally:
            if pending and not pending.done():
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
            await asyncio.to_thread(server.shutdown)
            await asyncio.to_thread(server.server_close)
            thread.join(timeout=2)
