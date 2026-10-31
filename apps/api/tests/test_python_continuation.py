"""Exercise task file continuity using the real snapshot store and a deterministic executor."""

import json
import threading

import pytest
from test_python_runtime import python_server as runtime_fixture
from test_python_runtime import request_for


@pytest.fixture
def python_server(monkeypatch):
    """Reuse the isolated runtime loader without executing Docker."""
    return runtime_fixture.__wrapped__(monkeypatch)


def test_task_files_continue_across_calls_and_runtime_restart(python_server, tmp_path, monkeypatch):
    """Inherit task files, not inputs; old replay cannot move the file head backwards."""
    runtime = python_server.PythonRuntime(tmp_path / "runtime", "sha256:" + "a" * 64)
    seen = []

    def execute(reference, scope, call_id, command, **kwargs):
        """Save source-labelled snapshots while preserving deterministic per-call replay."""
        with runtime.store.locked(reference, scope) as (directory, state):
            if call_id in state["calls"]:
                return state["calls"][call_id]["result"]
            files = runtime.store.snapshot_files(directory, state)
            seen.append(dict(files))
            files["result.txt"] = command["stdin"].encode()
            state["snapshot"] = runtime.store.save_snapshot(directory, files)
            result = {"status": "completed", "container_removed": True, "files": []}
            state["calls"][call_id] = {"result": result}
            runtime.store.save_state(directory, state)
            return result

    monkeypatch.setattr(runtime.executor, "execute", execute)
    first = request_for("first")
    first["output_files"] = []
    runtime.execute(first, threading.Event())
    second = request_for("second")
    second["output_files"] = []
    second["inputs"] = {}
    restarted = python_server.PythonRuntime(runtime.store.root, "sha256:" + "a" * 64)
    monkeypatch.setattr(restarted.executor, "execute", execute)
    restarted.execute(second, threading.Event())
    assert seen == [{}, {"result.txt": b"first"}]
    restarted.execute(first, threading.Event())
    third = request_for("third")
    third["output_files"] = []
    restarted.execute(third, threading.Event())
    assert seen[-1] == {"result.txt": b"second"}
    foreign = request_for("foreign")
    foreign["scope"]["user_id"] = "another-owner"
    foreign["output_files"] = []
    restarted.execute(foreign, threading.Event())
    assert seen[-1] == {}
    heads = [json.loads(path.read_text()) for path in runtime.store.root.glob("task-*.json")]
    assert len(heads) == 2 and all(head["pending_call"] is None for head in heads)


def test_pre_execution_cancellation_cannot_execute_on_replay(python_server, tmp_path, monkeypatch):
    """A known cancelled call stays cancelled even when retried without a cancellation flag."""
    runtime = python_server.PythonRuntime(tmp_path / "runtime", "sha256:" + "a" * 64)

    def forbidden(*args, **kwargs):
        """Fail if an already cancelled call reaches execution."""
        pytest.fail("Cancelled call executed")

    monkeypatch.setattr(runtime.executor, "execute", forbidden)
    request = request_for("print('must not run')")
    cancel = threading.Event()
    cancel.set()
    assert runtime.execute(request, cancel)["status"] == "cancelled"
    assert runtime.execute(request, threading.Event())["status"] == "cancelled"


def test_unknown_call_blocks_new_task_calls(python_server, tmp_path, monkeypatch):
    """An interrupted executor must be recovered by the same call, never skipped by new code."""
    runtime = python_server.PythonRuntime(tmp_path / "runtime", "sha256:" + "a" * 64)

    def unknown(*args, **kwargs):
        """Simulate loss of executor outcome after the durable pending marker."""
        raise RuntimeError("outcome unknown")

    monkeypatch.setattr(runtime.executor, "execute", unknown)
    with pytest.raises(RuntimeError, match="outcome unknown"):
        runtime.execute(request_for("first"), threading.Event())
    with pytest.raises(RuntimeError, match="requires recovery"):
        runtime.execute(request_for("second"), threading.Event())
