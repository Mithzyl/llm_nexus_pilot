"""Expose Python-only task execution on a private Unix socket, never the Docker socket.

Run with --root /private/task-root --socket /private/socket-dir/python.sock.
Requires an already-built nexuspilot-task-runtime:local image and local Docker.
Each call has immutable inputs and an independent workspace. Retained directories
are bounded; python_retention.py previews candidates but never deletes them.
External call records must survive any future workspace removal. This server is
not a cross-process workflow worker.
"""

import argparse
import base64
import fcntl
import hashlib
import json
import os
import socket
import socketserver
import threading
import time
import uuid
from pathlib import Path

from python_retention import call_lock, confirm_publication, read_record
from task_runtime import TaskExecutor, WorkspaceStore, atomic_json, relative_path, resolve_image

MAX_WIRE_BYTES = 24 * 1024 * 1024


def validate_request(value):
    """Reject commands other than Python and bound all wire fields before workspace creation."""
    if not isinstance(value, dict) or set(value) != {
        "scope", "call_id", "command", "inputs", "output_files",
    }:
        raise ValueError("Invalid request")
    if str(uuid.UUID(value["call_id"])) != value["call_id"]:
        raise ValueError("Invalid call ID")
    scope = value["scope"]
    if not isinstance(scope, dict) or set(scope) != {"user_id", "run_id", "task_id"} or any(
        not isinstance(v, str) or not 1 <= len(v) <= 128 for v in scope.values()
    ):
        raise ValueError("Invalid scope")
    command = value["command"]
    if not isinstance(command, dict) or set(command) != {"executable", "stdin"} or (
        command["executable"] != "python" or not isinstance(command["stdin"], str)
        or not 1 <= len(command["stdin"].encode()) <= 65536
    ):
        raise ValueError("Only Python stdin is supported")
    outputs = value["output_files"]
    if not isinstance(outputs, list) or len(outputs) > 8 or len(set(outputs)) != len(outputs):
        raise ValueError("Invalid outputs")
    for path in outputs:
        relative_path(path)
    encoded = value["inputs"]
    if not isinstance(encoded, dict) or len(encoded) > 8:
        raise ValueError("Invalid inputs")
    inputs = {}
    for name, body in encoded.items():
        relative_path(name)
        inputs[name] = base64.b64decode(body, validate=True)
    if any(len(b) > 8 * 1024 * 1024 for b in inputs.values()) or sum(
        map(len, inputs.values())
    ) > 16 * 1024 * 1024:
        raise ValueError("Input limit exceeded")
    return inputs


class PythonRuntime:
    """Reuse the local executor with durable per-call identity and bounded workspace retention."""

    def __init__(self, root, image_id, max_workspaces=128):
        """Keep Docker access in this process and serialize workspace identity allocation."""
        self.store = WorkspaceStore(root)
        self.executor = TaskExecutor(self.store, image_id=image_id, enabled=True)
        self.lock = threading.Lock()
        self.max_workspaces = max_workspaces

    def execute(self, request, cancel):
        """Lock durable call identity through execution, export and terminal receipt persistence."""
        validate_request(request)
        with call_lock(self.store.root, request["call_id"]):
            return self._execute(request, cancel)

    def confirm_publication(self, request):
        """Persist an API acknowledgement independently of disposable output snapshots."""
        return confirm_publication(self.store.root, request)

    def _execute(self, request, cancel):
        """Run once or replay retained results; missing workspace never allocates a replacement."""
        inputs = validate_request(request)
        scope, call_id = request["scope"], request["call_id"]
        fingerprint = hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()
        with self.lock, (self.store.root / ".allocation.lock").open("a") as allocation:
            fcntl.flock(allocation.fileno(), fcntl.LOCK_EX)
            index = self.store.root / f"call-{call_id}.json"
            if index.exists():
                saved = read_record(index)
                if saved["fingerprint"] != fingerprint:
                    raise ValueError("Call mismatch")
                reference = saved["reference"]
                if not (self.store.root / reference).is_dir():
                    return {"error": "python_result_unavailable",
                            "artifacts": saved.get("publication", {}).get("artifacts", [])}
            else:
                if sum(p.is_dir() for p in self.store.root.iterdir()) >= self.max_workspaces:
                    raise ValueError("Workspace retention limit reached")
                reference = self.store.create(
                    scope, inputs=inputs,
                    hashes={n: hashlib.sha256(b).hexdigest() for n, b in inputs.items()},
                    allow_execute=True,
                )
                atomic_json(index, {"reference": reference, "fingerprint": fingerprint,
                                    "scope": scope})
        if cancel.is_set():
            return {"status": "cancelled", "container_removed": True}
        result = self.executor.execute(
            reference, scope, call_id, request["command"], timeout_seconds=30, cancel=cancel,
        )
        result = dict(result)
        available = {f["path"] for f in result.pop("files", [])}
        result["files"] = {}
        if cancel.is_set():
            result["status"] = "cancelled"
        if result["status"] in {"completed", "failed"} and result["container_removed"]:
            for path in request["output_files"]:
                if path in available:
                    result["files"][path] = base64.b64encode(
                        self.store.read_file(reference, scope, path)
                    ).decode()
        saved = read_record(index)
        saved.setdefault("scope", scope)
        saved.setdefault("terminal", {
            "status": result["status"], "container_removed": result["container_removed"],
            "finished_at": time.time(),
            "outputs": {path: {"size_bytes": len(base64.b64decode(body)),
                               "content_hash": hashlib.sha256(base64.b64decode(body)).hexdigest()}
                        for path, body in result["files"].items()},
            "missing_outputs": [p for p in request["output_files"] if p not in result["files"]],
        })
        atomic_json(index, saved)
        result["retention_receipt_required"] = True
        return result


class Handler(socketserver.BaseRequestHandler):
    """Treat disconnect or an explicit cancellation frame as a request to stop the container."""

    def handle(self):
        """Bound input and concurrency; do not expose host exception text in failures."""
        if not self.server.slots.acquire(blocking=False):
            self.request.sendall(b'{"error":"python_runtime_busy"}\n')
            return
        cancel = threading.Event()
        stream = self.request.makefile("rb")
        watcher = None
        try:
            self.request.settimeout(10)
            raw = stream.readline(MAX_WIRE_BYTES + 1)
            if len(raw) > MAX_WIRE_BYTES or not raw.endswith(b"\n"):
                raise ValueError("Invalid frame")
            payload = json.loads(raw)
            if payload.get("operation") == "confirm_publication":
                result = self.server.runtime.confirm_publication(payload)
                self.request.sendall(json.dumps(result).encode() + b"\n")
                return
            validate_request(payload)
            self.request.settimeout(None)

            def watch_disconnect():
                """Any extra frame or EOF stops execution; there are no further commands."""
                try:
                    stream.readline(1024)
                except OSError:
                    pass
                cancel.set()

            watcher = threading.Thread(target=watch_disconnect, daemon=True)
            watcher.start()
            result = self.server.runtime.execute(payload, cancel)
            encoded = json.dumps(result).encode() + b"\n"
            if len(encoded) > MAX_WIRE_BYTES:
                raise ValueError("Result limit exceeded")
            self.request.sendall(encoded)
        except Exception:
            cancel.set()
            try:
                self.request.sendall(b'{"error":"python_execution_unavailable"}\n')
            except OSError:
                pass
        finally:
            try:
                self.request.shutdown(socket.SHUT_RD)
            except OSError:
                pass
            if watcher:
                watcher.join(timeout=1)
            stream.close()
            self.server.slots.release()


class Server(socketserver.ThreadingUnixStreamServer):
    """Wait for active requests during shutdown rather than abandoning Docker cleanup."""

    daemon_threads = False

    def __init__(self, path, runtime):
        """Bind only in an owner-private directory; never remove an existing socket or file."""
        parent = Path(path).parent
        info = parent.stat()
        if not Path(path).is_absolute() or parent.is_symlink() or (
            info.st_uid != os.getuid() or info.st_mode & 0o077
        ):
            raise ValueError("Socket parent must be private")
        self.runtime = runtime
        self.slots = threading.BoundedSemaphore(2)
        super().__init__(str(path), Handler)
        os.chmod(path, 0o600)


def main():
    """Start the administrator-managed Python-only socket with a locally resolved image."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--socket", required=True, type=Path)
    parser.add_argument("--image", default="nexuspilot-task-runtime:local")
    args = parser.parse_args()
    runtime = PythonRuntime(args.root, resolve_image(args.image))
    with Server(args.socket, runtime) as server:
        try:
            server.serve_forever()
        finally:
            args.socket.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
