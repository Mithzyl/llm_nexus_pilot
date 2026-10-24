"""Read-only workspace cleanup preview and compact publication receipts.

Run: python3 deployments/task-runtime/python_retention.py --root /absolute/runtime/root
No Docker, database or MinIO credentials are required. This command never deletes
or creates files. Eligibility is a point-in-time preview, not permission to delete.
"""

import argparse
import contextlib
import fcntl
import json
import os
import time
import uuid
from pathlib import Path

from task_runtime import atomic_json


@contextlib.contextmanager
def call_lock(root, call_id):
    """Serialize execution and acknowledgement outside the disposable workspace."""
    if str(uuid.UUID(call_id)) != call_id:
        raise ValueError("Invalid call identity")
    descriptor = os.open(root / f"call-{call_id}.lock",
                         os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(descriptor)


def read_record(path):
    """Read bounded executor metadata without following a substituted final symlink."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as stream:
        data = stream.read(256 * 1024 + 1)
    if len(data) > 256 * 1024:
        raise ValueError("Metadata too large")
    value = json.loads(data)
    if not isinstance(value, dict):
        raise ValueError("Metadata must be an object")
    return value


def confirm_publication(root, request):
    """Accept a trusted API receipt only for the exact scope and exported byte manifest."""
    if set(request) != {"operation", "scope", "call_id", "artifacts"}:
        raise ValueError("Invalid receipt")
    call_id = request["call_id"]
    with call_lock(root, call_id):
        path = root / f"call-{call_id}.json"
        saved = read_record(path)
        terminal = saved.get("terminal")
        if saved.get("scope") != request["scope"] or not terminal:
            raise ValueError("Receipt scope or outcome unavailable")
        manifest = terminal["outputs"]
        artifacts = request["artifacts"]
        if not isinstance(artifacts, list) or len(artifacts) != len(manifest):
            raise ValueError("Incomplete publication")
        received = {}
        identities = set()
        for item in artifacts:
            if set(item) != {"path", "artifact_id", "size_bytes", "content_hash"}:
                raise ValueError("Invalid artifact receipt")
            if str(uuid.UUID(item["artifact_id"])) != item["artifact_id"]:
                raise ValueError("Invalid artifact identity")
            if item["path"] in received or item["artifact_id"] in identities:
                raise ValueError("Duplicate artifact receipt")
            identities.add(item["artifact_id"])
            received[item["path"]] = {k: item[k] for k in ("size_bytes", "content_hash")}
        if received != manifest or terminal.get("missing_outputs"):
            raise ValueError("Publication does not match exported files")
        if "publication" in saved:
            if saved["publication"]["artifacts"] != artifacts:
                raise ValueError("Publication already confirmed differently")
        else:
            saved["publication"] = {"confirmed_at": time.time(), "artifacts": artifacts}
            atomic_json(path, saved)
        return {"confirmed": True}


def preview(root, now=None):
    """Inspect retained directories without mutation; uncertain or busy records stay blocked."""
    root = Path(root)
    if not root.is_absolute() or root.is_symlink() or not root.is_dir():
        raise ValueError("Existing absolute runtime root required")
    now = time.time() if now is None else now
    results = []
    known = set()
    for index in sorted(root.glob("call-*.json")):
        row = {"call_id": index.stem[5:], "eligible": False, "reason": "invalid_metadata"}
        fd = None
        workspace_fd = None
        try:
            call_id = str(uuid.UUID(row["call_id"]))
            initial = read_record(index)
            reference = initial["reference"]
            if str(uuid.UUID(reference)) != reference:
                raise ValueError("Invalid reference")
            known.add(reference)
            row["workspace_reference"] = reference
            # No O_CREAT: preview must not change even legacy workspaces.
            fd = os.open(root / f"call-{call_id}.lock", os.O_RDONLY | os.O_NOFOLLOW)
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            saved = read_record(index)
            reference = saved["reference"]
            if str(uuid.UUID(reference)) != reference:
                raise ValueError("Invalid reference")
            known.add(reference)
            directory = root / reference
            row["workspace_reference"] = reference
            if not directory.exists():
                row["reason"] = "workspace_missing_record_retained"
            elif directory.is_symlink():
                row["reason"] = "invalid_workspace"
            else:
                lock_path = directory / ".lock"
                if lock_path.exists():
                    workspace_fd = os.open(lock_path, os.O_RDONLY | os.O_NOFOLLOW)
                    fcntl.flock(workspace_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                state = read_record(directory / "state.json")
                row["size_bytes"] = sum(
                    file.lstat().st_size
                    for base, dirs, files in os.walk(directory, followlinks=False)
                    for file in (Path(base) / name for name in files) if not file.is_symlink()
                )
                terminal = saved.get("terminal")
                if state.get("scope") != saved.get("scope"):
                    row["reason"] = "legacy_or_scope_mismatch"
                elif state.get("state") != "ready" or state.get("container"):
                    row["reason"] = "execution_or_cleanup_unconfirmed"
                elif not terminal or not terminal.get("container_removed"):
                    row["reason"] = "execution_unconfirmed"
                elif not saved.get("publication"):
                    row["reason"] = "publication_pending"
                else:
                    hours = 24 if terminal["status"] == "completed" else 72
                    row["eligible_after"] = max(terminal["finished_at"],
                        saved["publication"]["confirmed_at"]) + hours * 3600
                    row["eligible"] = now >= row["eligible_after"]
                    row["reason"] = "eligible_preview_only" if row["eligible"] else "retained"
        except BlockingIOError:
            row["reason"] = "busy"
        except FileNotFoundError:
            row["reason"] = "legacy_or_missing_metadata"
        except (OSError, ValueError, KeyError, TypeError):
            pass
        finally:
            if fd is not None:
                os.close(fd)
            if workspace_fd is not None:
                os.close(workspace_fd)
        results.append(row)
    for directory in sorted(root.iterdir()):
        if directory.is_dir() and directory.name not in known:
            results.append({"workspace_reference": directory.name, "eligible": False,
                            "reason": "unindexed_or_legacy_workspace"})
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    print(json.dumps({"read_only": True, "workspaces": preview(parser.parse_args().root)},
                     ensure_ascii=False, indent=2))
