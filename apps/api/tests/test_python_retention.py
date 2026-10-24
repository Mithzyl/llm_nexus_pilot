"""Verify durable receipts and read-only previews without Docker or deleting user data."""

import json
import threading
import time

import pytest
from test_python_runtime import python_server as runtime_fixture
from test_python_runtime import request_for


@pytest.fixture
def python_server(monkeypatch):
    """Reuse the executor module loader without requiring Docker."""
    return runtime_fixture.__wrapped__(monkeypatch)


def completed_runtime(python_server, tmp_path, monkeypatch):
    """Create a synthetic no-output executor that counts actual execution attempts."""
    runtime = python_server.PythonRuntime(tmp_path / "runtime", "sha256:" + "a" * 64)
    calls = []

    def execute(*args, **kwargs):
        """Return a terminal execution without running Docker."""
        calls.append(1)
        return {"status": "completed", "container_removed": True, "files": []}

    monkeypatch.setattr(runtime.executor, "execute", execute)
    request = request_for("print(1)")
    request["output_files"] = []
    runtime.execute(request, threading.Event())
    return runtime, request, calls


def test_receipt_required_before_readonly_preview(python_server, tmp_path, monkeypatch):
    """A finished directory remains blocked until the API confirms publication or no outputs."""
    runtime, request, _ = completed_runtime(python_server, tmp_path, monkeypatch)
    from python_retention import preview
    before = {str(p): p.read_bytes() for p in runtime.store.root.rglob("*") if p.is_file()}
    pending = preview(runtime.store.root, now=time.time() + 90000)[0]
    assert pending["reason"] == "publication_pending"
    assert before == {str(p): p.read_bytes() for p in runtime.store.root.rglob("*") if p.is_file()}
    receipt = {"operation": "confirm_publication", "scope": request["scope"],
               "call_id": request["call_id"], "artifacts": []}
    runtime.confirm_publication(receipt)
    assert preview(runtime.store.root)[0]["reason"] == "retained"
    assert preview(runtime.store.root, now=time.time() + 90000)[0]["eligible"]
    assert runtime.confirm_publication(receipt)["confirmed"]
    with pytest.raises(ValueError):
        runtime.confirm_publication({**receipt, "scope": {**request["scope"], "user_id": "other"}})


def test_missing_workspace_never_executes_again(python_server, tmp_path, monkeypatch):
    """Even after test-owned workspace removal and process recreation, the call cannot rerun."""
    runtime, request, calls = completed_runtime(python_server, tmp_path, monkeypatch)
    index = runtime.store.root / f"call-{request['call_id']}.json"
    saved = json.loads(index.read_text())
    (runtime.store.root / saved["reference"]).rename(tmp_path / "removed-test-workspace")
    restored = python_server.PythonRuntime(runtime.store.root, "sha256:" + "a" * 64)
    monkeypatch.setattr(restored.executor, "execute", runtime.executor.execute)
    result = restored.execute(request, threading.Event())
    assert result["error"] == "python_result_unavailable"
    assert calls == [1]
    with pytest.raises(ValueError):
        restored.execute({**request, "command": {"executable": "python", "stdin": "print(2)"}},
                         threading.Event())


@pytest.mark.parametrize("condition", ["busy", "executing", "missing", "legacy"])
def test_preview_blocks_uncertain_workspaces(python_server, tmp_path, monkeypatch, condition):
    """Busy, unknown, incompletely published and legacy workspaces never become candidates."""
    from python_retention import call_lock, preview
    from task_runtime import atomic_json
    runtime, request, _ = completed_runtime(python_server, tmp_path, monkeypatch)
    index = runtime.store.root / f"call-{request['call_id']}.json"
    record = json.loads(index.read_text())
    if condition == "busy":
        with call_lock(runtime.store.root, request["call_id"]):
            assert preview(runtime.store.root)[0]["reason"] == "busy"
        return
    if condition == "legacy":
        record.pop("terminal")
    elif condition == "missing":
        record["terminal"]["missing_outputs"] = ["missing.csv"]
        atomic_json(index, record)
        with pytest.raises(ValueError):
            runtime.confirm_publication({"operation": "confirm_publication",
                "scope": request["scope"], "call_id": request["call_id"], "artifacts": []})
    else:
        path = runtime.store.root / record["reference"] / "state.json"
        state = json.loads(path.read_text())
        state["state"] = "executing"
        atomic_json(path, state)
    atomic_json(index, record)
    assert not preview(runtime.store.root, now=time.time() + 999999)[0]["eligible"]


def test_receipt_rejects_incomplete_or_changed_manifest(python_server, tmp_path, monkeypatch):
    """Only complete exact output metadata is acknowledged; conflicts cannot replace a receipt."""
    import uuid

    from task_runtime import atomic_json
    runtime, request, _ = completed_runtime(python_server, tmp_path, monkeypatch)
    index = runtime.store.root / f"call-{request['call_id']}.json"
    saved = json.loads(index.read_text())
    saved["terminal"]["outputs"] = {"result.csv": {"size_bytes": 3, "content_hash": "abc"}}
    atomic_json(index, saved)
    receipt = {"operation": "confirm_publication", "scope": request["scope"],
               "call_id": request["call_id"], "artifacts": []}
    with pytest.raises(ValueError):
        runtime.confirm_publication(receipt)
    receipt["artifacts"] = [{"path": "result.csv", "artifact_id": str(uuid.uuid4()),
                             "size_bytes": 4, "content_hash": "abc"}]
    with pytest.raises(ValueError):
        runtime.confirm_publication(receipt)
    receipt["artifacts"][0]["size_bytes"] = 3
    assert runtime.confirm_publication(receipt)["confirmed"]
    receipt["artifacts"][0]["artifact_id"] = str(uuid.uuid4())
    with pytest.raises(ValueError):
        runtime.confirm_publication(receipt)


@pytest.mark.parametrize("status", ["failed", "cancelled", "timed_out"])
def test_non_success_receipts_use_longer_preview_retention(
    python_server, tmp_path, monkeypatch, status,
):
    """Confirmed non-success calls require 72 hours, not the successful-call 24 hours."""
    from python_retention import preview
    from task_runtime import atomic_json
    runtime, request, _ = completed_runtime(python_server, tmp_path, monkeypatch)
    index = runtime.store.root / f"call-{request['call_id']}.json"
    saved = json.loads(index.read_text())
    saved["terminal"]["status"] = status
    atomic_json(index, saved)
    runtime.confirm_publication({"operation": "confirm_publication", "scope": request["scope"],
                                 "call_id": request["call_id"], "artifacts": []})
    assert not preview(runtime.store.root, now=time.time() + 90000)[0]["eligible"]
    assert preview(runtime.store.root, now=time.time() + 260000)[0]["eligible"]
