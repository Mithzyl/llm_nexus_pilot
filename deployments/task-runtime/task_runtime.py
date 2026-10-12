"""Local administrative task executor; NOT an API or model-callable entry point.

Uses an already-built trusted Linux image on Docker Desktop/macOS or Linux.
Host code never executes submitted Python/Shell. Work files live in a bounded
container tmpfs, then pass archive validation before a private host snapshot is
saved. Identity arguments must come from a trusted caller; they are not login
credentials. Registry/DB/Artifact integration is deliberately not enabled here.
See docs/implementation/phase-7-tool-runtime-plan.md for the integration status.
"""

import argparse
import contextlib
import fcntl
import hashlib
import io
import json
import os
import re
import selectors
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import uuid
from pathlib import Path, PurePosixPath

MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 16 * 1024 * 1024
MAX_FILES = 128
MAX_ARCHIVE_BYTES = MAX_TOTAL_BYTES + 2 * 1024 * 1024
MAX_STREAM_BYTES = 32768
MAX_CALLS = 32
MAX_STDIN_BYTES = 65536


class FileSnapshot(dict):
    """Hold regular-file bytes plus directories so empty working folders survive calls."""

    def __init__(self):
        """Start with no files or directories; validation remains at every storage boundary."""
        super().__init__()
        self.directories = set()


def relative_path(value: str) -> str:
    """Reject traversal, platform aliases and control characters before filesystem use."""
    if (
        not isinstance(value, str)
        or not value
        or len(value.encode("utf-8")) > 240
        or "\\" in value
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
        or value.startswith("/")
        or any(part in {"", ".", ".."} for part in value.split("/"))
    ):
        raise ValueError("Invalid relative path")
    return value


def validate_files(files: dict[str, bytes]) -> None:
    """Bound file count, individual sizes, total bytes and file/directory collisions."""
    if len(files) > MAX_FILES or sum(len(value) for value in files.values()) > MAX_TOTAL_BYTES:
        raise ValueError("File count or total byte limit exceeded")
    for name, content in files.items():
        relative_path(name)
        if not isinstance(content, bytes) or len(content) > MAX_FILE_BYTES:
            raise ValueError("File byte limit exceeded")
        if any(str(parent) in files for parent in PurePosixPath(name).parents):
            raise ValueError("File and directory paths conflict")
    directories = getattr(files, "directories", set())
    if len(directories) + len(files) > MAX_FILES * 8:
        raise ValueError("Directory entry limit exceeded")
    for name in directories:
        relative_path(name)
        if name in files or any(str(parent) in files for parent in PurePosixPath(name).parents):
            raise ValueError("File and directory paths conflict")


def read_regular_tree(root: Path) -> dict[str, bytes]:
    """Read a private tree without following links; reject missing roots and oversized files."""
    if root.is_symlink() or not root.is_dir():
        raise ValueError("Invalid snapshot or input directory")
    files = FileSnapshot()
    for index, path in enumerate(root.rglob("*")):
        if index >= MAX_FILES * 8:
            raise ValueError("Directory entry limit exceeded")
        info = path.lstat()
        if stat.S_ISDIR(info.st_mode):
            files.directories.add(path.relative_to(root).as_posix())
            continue
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > MAX_FILE_BYTES:
            raise ValueError("Invalid snapshot or input file")
        files[path.relative_to(root).as_posix()] = path.read_bytes()
        validate_files(files)
    validate_files(files)
    return files


def encode_snapshot(files: dict[str, bytes]) -> bytes:
    """Create a bounded archive with safe permissions owned by the unprivileged runtime UID."""
    validate_files(files)
    result = io.BytesIO()
    with tarfile.open(fileobj=result, mode="w") as archive:
        directories = set()
        for name in sorted(getattr(files, "directories", set())):
            info = tarfile.TarInfo(name)
            info.type, info.mode, info.uid, info.gid = tarfile.DIRTYPE, 0o700, 65532, 65532
            archive.addfile(info)
            directories.add(name)
        for name, content in sorted(files.items()):
            for parent in reversed(PurePosixPath(name).parents):
                if str(parent) == "." or str(parent) in directories:
                    continue
                info = tarfile.TarInfo(str(parent))
                info.type, info.mode, info.uid, info.gid = (
                    tarfile.DIRTYPE,
                    0o700,
                    65532,
                    65532,
                )
                archive.addfile(info)
                directories.add(str(parent))
            info = tarfile.TarInfo(name)
            info.mode, info.uid, info.gid, info.size = 0o600, 65532, 65532, len(content)
            archive.addfile(info, io.BytesIO(content))
    return result.getvalue()


def decode_snapshot(content: bytes) -> dict[str, bytes]:
    """Read regular files without tar extraction; reject links, special files and duplicates."""
    if len(content) > MAX_ARCHIVE_BYTES:
        raise ValueError("Archive byte limit exceeded")
    files, seen, total = FileSnapshot(), set(), 0
    with tarfile.open(fileobj=io.BytesIO(content), mode="r:") as archive:
        for info in archive:
            name = info.name.removeprefix("./")
            if name in {"", "."} and info.isdir():
                continue
            relative_path(name)
            if name in seen or len(seen) >= MAX_FILES * 8:
                raise ValueError("Archive duplicate or entry limit exceeded")
            seen.add(name)
            if info.isdir():
                files.directories.add(name)
                continue
            if not info.isreg() or info.sparse is not None:
                raise ValueError("Output must contain only regular files and directories")
            total += info.size
            if (
                not 0 <= info.size <= MAX_FILE_BYTES
                or total > MAX_TOTAL_BYTES
                or len(files) >= MAX_FILES
            ):
                raise ValueError("Output file byte/count limit exceeded")
            source = archive.extractfile(info)
            files[name] = source.read(MAX_FILE_BYTES + 1)
            if len(files[name]) != info.size:
                raise ValueError("Incomplete output file")
    validate_files(files)
    return files


def atomic_json(path: Path, value: dict) -> None:
    """Fsync a private replacement before publishing state; never overwrite it in place."""
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".state-", delete=False) as target:
        temporary = Path(target.name)
        try:
            target.write(json.dumps(value, sort_keys=True, ensure_ascii=False).encode())
            target.flush()
            os.fsync(target.fileno())
            os.replace(temporary, path)
            descriptor = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        finally:
            temporary.unlink(missing_ok=True)


class WorkspaceStore:
    """Persist private task snapshots with exact scope checks and cross-process file locks."""

    def __init__(self, root: Path):
        """Accept only an explicitly selected, owner-only root outside container write access."""
        root = Path(root)
        if not root.is_absolute() or root.is_symlink() or "," in str(root):
            raise ValueError("Workspace root must be absolute and private")
        root.mkdir(mode=0o700, parents=False, exist_ok=True)
        info = root.stat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError("Workspace root must be private to the executor user")
        self.root = root.resolve()

    def create(self, scope, *, inputs=None, hashes=None, allow_execute=False) -> str:
        """Create a fresh identity-bound workspace after validating all immutable input bytes."""
        if (
            not isinstance(scope, dict)
            or set(scope) != {"user_id", "run_id", "task_id"}
            or any(
                not isinstance(value, str) or not 1 <= len(value) <= 128 for value in scope.values()
            )
            or not isinstance(allow_execute, bool)
        ):
            raise ValueError("Invalid trusted task scope")
        inputs, hashes = inputs or {}, hashes or {}
        validate_files(inputs)
        if set(inputs) != set(hashes) or any(
            hashlib.sha256(content).hexdigest() != hashes[name] for name, content in inputs.items()
        ):
            raise ValueError("Input hash mismatch")
        reference = str(uuid.uuid4())
        directory = self.root / reference
        directory.mkdir(mode=0o700)
        (directory / "inputs").mkdir(mode=0o755)
        (directory / "snapshots").mkdir(mode=0o700)
        (directory / "calls").mkdir(mode=0o700)
        for name, content in inputs.items():
            target = directory / "inputs" / name
            target.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
            target.write_bytes(content)
            target.chmod(0o444)
        for path in [directory / "inputs", *(directory / "inputs").rglob("*")]:
            if path.is_dir():
                path.chmod(0o555)
        # Containers receive only this immutable directory through a read-only mount.
        self.save_state(
            directory,
            {
                "version": 1,
                "scope": dict(scope),
                "allow_execute": allow_execute,
                "state": "ready",
                "snapshot": None,
                "inputs": hashes,
                "calls": {},
            },
        )
        return reference

    @contextlib.contextmanager
    def locked(self, reference, scope):
        """Check exact task ownership under a non-blocking lock; never follow host links."""
        if str(uuid.UUID(reference)) != reference:
            raise ValueError("Invalid workspace reference")
        directory = self.root / reference
        if directory.is_symlink() or not directory.is_dir():
            raise FileNotFoundError("Workspace unavailable")
        descriptor = os.open(directory / ".lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            state_path = directory / "state.json"
            if state_path.is_symlink() or state_path.stat().st_size > 16 * 1024 * 1024:
                raise ValueError("Invalid workspace state")
            state = json.loads(state_path.read_bytes())
            if state["scope"] != scope:
                raise PermissionError("Workspace unavailable for this task")
            yield directory, state
        finally:
            os.close(descriptor)

    def save_state(self, directory: Path, state: dict) -> None:
        """Publish durable state; a failed save must leave the last recorded execution visible."""
        atomic_json(directory / "state.json", state)

    def snapshot_files(self, directory: Path, state: dict) -> dict[str, bytes]:
        """Read only a previously validated host snapshot, checking links and byte limits again."""
        if state["snapshot"] is None:
            return {}
        snapshot = str(uuid.UUID(state["snapshot"]))
        if (directory / "snapshots").is_symlink():
            raise ValueError("Invalid snapshot parent")
        return read_regular_tree(directory / "snapshots" / snapshot)

    def save_snapshot(self, directory: Path, files: dict[str, bytes]) -> str:
        """Save a new immutable generation before publishing its pointer; retain earlier results."""
        validate_files(files)
        snapshot = str(uuid.uuid4())
        root = directory / "snapshots" / snapshot
        root.mkdir(mode=0o700)
        for name in sorted(getattr(files, "directories", set())):
            (root / name).mkdir(mode=0o700, parents=True, exist_ok=True)
        for name, content in files.items():
            target = root / name
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            with target.open("xb") as output:
                output.write(content)
                output.flush()
                os.fsync(output.fileno())
        # File fsync alone does not persist directory entries after a sudden host restart.
        for folder in [*(path for path in root.rglob("*") if path.is_dir()), root, root.parent]:
            descriptor = os.open(folder, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        return snapshot

    def read_file(self, reference, scope, name) -> bytes:
        """Read a saved task result under the same lock and ownership checks as execution."""
        relative_path(name)
        with self.locked(reference, scope) as (directory, state):
            if state["state"] != "ready":
                raise RuntimeError("Workspace requires recovery before reading outputs")
            files = self.snapshot_files(directory, state)
            if name not in files:
                raise FileNotFoundError(name)
            return files[name]


def command_arguments(command: dict) -> list[str]:
    """Map fixed interpreter IDs to isolated stdin/script execution, never shell=True."""
    if not isinstance(command, dict) or set(command) - {
        "executable",
        "arguments",
        "stdin",
        "cwd",
    }:
        raise ValueError("Invalid command fields")
    arguments = command.get("arguments", [])
    stdin = command.get("stdin", "")
    cwd = command.get("cwd", ".")
    if (
        not isinstance(arguments, list)
        or len(arguments) > 32
        or any(not isinstance(arg, str) or len(arg) > 1024 or "\x00" in arg for arg in arguments)
        or not isinstance(stdin, str)
        or len(stdin.encode()) > MAX_STDIN_BYTES
    ):
        raise ValueError("Command argument or stdin limit exceeded")
    if cwd != ".":
        relative_path(cwd)
    script = "-"
    if arguments:
        script = relative_path(arguments[0])
        if script.startswith("-"):
            raise ValueError("Interpreter options are not accepted")
    if command.get("executable") == "python":
        return ["/usr/local/bin/python", "-I", "-B", "-u", script, *arguments[1:]]
    if command.get("executable") == "shell":
        return [
            "/bin/sh",
            *(["-s", "--"] if not arguments else ["--", script]),
            *arguments[1:],
        ]
    raise ValueError("Unknown interpreter")


def docker(arguments, *, input_bytes=None, timeout=30, check=True):
    """Run only trusted Docker argv; never inherit stdin or print daemon errors to model output."""
    return subprocess.run(
        ["docker", *arguments],
        input=input_bytes,
        capture_output=True,
        timeout=timeout,
        check=check,
    )


def resolve_image(tag: str) -> str:
    """Pin a local Linux image; missing images never trigger a pull or host execution."""
    output = (
        docker(["image", "inspect", "--format", "{{.Os}} {{.Id}}", tag]).stdout.decode().strip()
    )
    if not re.fullmatch(r"linux sha256:[0-9a-f]{64}", output):
        raise ValueError("A local trusted Linux image is required")
    return output.split()[1]


def output_text(content: bytes, truncated: bool) -> tuple[str, bool]:
    """Bound UTF-8 bytes even when invalid binary output expands into replacement characters."""
    encoded = content.decode("utf-8", errors="replace").encode("utf-8")
    return encoded[:MAX_STREAM_BYTES].decode("utf-8", errors="ignore"), (
        truncated or len(encoded) > MAX_STREAM_BYTES
    )


def capture_process(arguments, *, stdin=b"", timeout=30, cancel=None, limit=MAX_STREAM_BYTES):
    """Drain both pipes concurrently; cap retained bytes, and kill the CLI on cancel/deadline.

    Terminating the CLI does not stop container processes. The caller must pause
    and remove the exact container before reporting completed cancellation.
    """
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    truncated = {"stdout": False, "stderr": False}
    reason = None
    with tempfile.TemporaryFile() as source:
        source.write(stdin)
        source.seek(0)
        with subprocess.Popen(
            arguments, stdin=source, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        ) as process:
            try:
                with selectors.DefaultSelector() as selector:
                    selector.register(process.stdout, selectors.EVENT_READ, "stdout")
                    selector.register(process.stderr, selectors.EVENT_READ, "stderr")
                    deadline = time.monotonic() + timeout
                    while selector.get_map():
                        if cancel is not None and cancel.is_set():
                            reason = "cancelled"
                            break
                        if time.monotonic() >= deadline:
                            reason = "timed_out"
                            break
                        for key, _ in selector.select(0.05):
                            chunk = os.read(key.fileobj.fileno(), 65536)
                            if not chunk:
                                selector.unregister(key.fileobj)
                                continue
                            available = max(0, limit - len(buffers[key.data]))
                            buffers[key.data].extend(chunk[:available])
                            truncated[key.data] |= len(chunk) > available
                    if reason:
                        process.kill()
                    process.wait(timeout=5)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)
    return process.returncode, buffers, truncated, reason


class TaskExecutor:
    """Execute one serialized task call and record its result before container cleanup."""

    def __init__(self, store: WorkspaceStore, *, image_id: str, enabled=False):
        """Keep execution opt-in independent of per-task authorization and image availability."""
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
            raise ValueError("Execution requires a pinned image ID")
        self.store, self.image_id, self.enabled = store, image_id, enabled

    @contextlib.contextmanager
    def execution_slot(self):
        """Allow at most two simultaneous containers across executor processes using this root."""
        descriptor = None
        for index in range(2):
            candidate = os.open(
                self.store.root / f".slot-{index}",
                os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW,
                0o600,
            )
            try:
                fcntl.flock(candidate, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                os.close(candidate)
                continue
            descriptor = candidate
            break
        if descriptor is None:
            raise BlockingIOError("Task executor is busy")
        try:
            yield
        finally:
            os.close(descriptor)

    def create_container(self, name, directory):
        """Mount verified inputs; writable memory is quota-limited, without host write access."""
        docker(
            [
                "create",
                "--name",
                name,
                "--pull",
                "never",
                "--init",
                "--network",
                "none",
                "--read-only",
                "--user",
                "65532:65532",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges:true",
                "--memory",
                "256m",
                "--memory-swap",
                "256m",
                "--cpus",
                "1",
                "--pids-limit",
                "64",
                "--ulimit",
                "nofile=128:128",
                "--ulimit",
                "core=0:0",
                "--log-driver",
                "none",
                "--tmpfs",
                "/tmp:rw,noexec,nosuid,nodev,size=16777216,mode=1777",
                "--tmpfs",
                "/workspace:rw,noexec,nosuid,nodev,size=33554432,mode=700,uid=65532,gid=65532",
                "--mount",
                f"type=bind,src={directory / 'inputs'},dst=/input,readonly",
                "--workdir",
                "/workspace",
                "--entrypoint",
                "/bin/sleep",
                self.image_id,
                "300",
            ]
        )

    def has_task_processes(self, name, retained_pids):
        """Require only Docker init and the fixed sleep before exporting the tmpfs snapshot.

        No submitted process may remain to mutate files while tar reads them. The
        two retained processes do not spawn children. Process names are not used
        as an allowlist because submitted code can choose its own process name.
        """
        for _ in range(10):
            rows = docker(["top", name, "-eo", "pid"]).stdout.splitlines()[1:]
            if {int(row) for row in rows} == retained_pids:
                return False
            time.sleep(0.05)
        return True

    def execute(self, reference, scope, call_id, command, *, timeout_seconds=30, cancel=None):
        """Persist one call; unknown or save-failed outcomes block reuse instead of rerunning."""
        if not self.enabled:
            raise PermissionError("Task execution is disabled")
        argv = command_arguments(command)
        if (
            str(uuid.UUID(call_id)) != call_id
            or type(timeout_seconds) is not int
            or not 1 <= timeout_seconds <= 60
        ):
            raise ValueError("Invalid call identity or timeout")
        command_hash = hashlib.sha256(
            json.dumps([command, timeout_seconds], sort_keys=True).encode()
        ).hexdigest()
        with (
            self.store.locked(reference, scope) as (directory, state),
            self.execution_slot(),
        ):
            if not state["allow_execute"]:
                raise PermissionError("This task cannot execute code")
            if state["state"] != "ready":
                raise RuntimeError("Previous execution outcome requires administrative recovery")
            if call_id in state["calls"]:
                previous = state["calls"][call_id]
                if previous["command_hash"] != command_hash:
                    raise ValueError("Call identity was reused with different arguments")
                return previous["result"]
            if len(state["calls"]) >= MAX_CALLS:
                raise ValueError("Task command count limit exceeded")
            files = self.store.snapshot_files(directory, state)
            cwd = command.get("cwd", ".")
            if command.get("arguments"):
                script = str(PurePosixPath(cwd) / command["arguments"][0])
                if script not in files:
                    raise FileNotFoundError("Script is not in the current task snapshot")
            # Verify inputs again before exposing them, including host-side link tampering.
            input_files = read_regular_tree(directory / "inputs")
            if {
                key: hashlib.sha256(value).hexdigest() for key, value in input_files.items()
            } != state["inputs"]:
                raise ValueError("Immutable input hash or file type changed")
            name = "nexuspilot-task-" + uuid.uuid4().hex
            state.update(state="executing", container=name, active_call_id=call_id)
            atomic_json(
                directory / "calls" / f"{call_id}.json",
                {
                    "command": command,
                    "command_hash": command_hash,
                    "image_id": self.image_id,
                    "timeout_seconds": timeout_seconds,
                },
            )
            self.store.save_state(directory, state)
            started = False
            try:
                self.create_container(name, directory)
                docker(["start", name])
                retained_pids = {
                    int(row) for row in docker(["top", name, "-eo", "pid"]).stdout.splitlines()[1:]
                }
                if len(retained_pids) != 2:
                    raise RuntimeError("Unexpected task container startup processes")
                docker(
                    ["exec", "-i", name, "/bin/tar", "-xf", "-", "-C", "/workspace"],
                    input_bytes=encode_snapshot(files),
                )
                started = True
                exit_code, buffers, truncated, reason = capture_process(
                    [
                        "docker",
                        "exec",
                        "-i",
                        "--workdir",
                        str(PurePosixPath("/workspace") / cwd),
                        name,
                        *argv,
                    ],
                    stdin=command.get("stdin", "").encode(),
                    timeout=timeout_seconds,
                    cancel=cancel,
                )
                stdout, stdout_truncated = output_text(
                    bytes(buffers["stdout"]), truncated["stdout"]
                )
                stderr, stderr_truncated = output_text(
                    bytes(buffers["stderr"]), truncated["stderr"]
                )
                result = {
                    "status": reason or ("completed" if exit_code == 0 else "failed"),
                    "exit_code": None if reason else exit_code,
                    "stdout": stdout,
                    "stderr": stderr,
                    "stdout_truncated": stdout_truncated,
                    "stderr_truncated": stderr_truncated,
                    "image_id": self.image_id,
                    "container_removed": False,
                    "error_code": None,
                }
                output_files = None
                if reason or self.has_task_processes(name, retained_pids):
                    # No partial snapshot: kill descendants and keep the previous saved generation.
                    docker(["rm", "--force", name])
                    result["container_removed"] = True
                    if not reason:
                        result.update(
                            status="failed", error_code="background_processes_not_supported"
                        )
                else:
                    # docker cp/export omit tmpfs mounts on Docker Desktop. Only fixed tar runs now.
                    code, exported, overflow, export_reason = capture_process(
                        ["docker", "exec", name, "/bin/tar", "-cf", "-", "-C", "/workspace", "."],
                        timeout=15,
                        limit=MAX_ARCHIVE_BYTES,
                    )
                    docker(["pause", name])
                    if code or export_reason:
                        raise RuntimeError("Task snapshot export failed; paused container retained")
                    try:
                        if overflow["stdout"]:
                            raise ValueError("Output archive limit exceeded")
                        output_files = decode_snapshot(bytes(exported["stdout"]))
                    except (ValueError, tarfile.TarError):
                        result.update(status="failed", error_code="invalid_output_files")
                if output_files is not None:
                    state["snapshot"] = self.store.save_snapshot(directory, output_files)
                    result["files"] = [
                        {
                            "path": path,
                            "size_bytes": len(content),
                            "content_hash": hashlib.sha256(content).hexdigest(),
                        }
                        for path, content in sorted(output_files.items())
                    ]
                else:
                    result["files"] = []
                state["calls"][call_id] = {
                    "command_hash": command_hash,
                    "result": result,
                }
                state["state"] = "cleanup_pending"
                self.store.save_state(directory, state)
                if not result["container_removed"]:
                    docker(["rm", "--force", name])
                result["container_removed"] = True
                state.update(state="ready", container=None, active_call_id=None)
                self.store.save_state(directory, state)
                return result
            except BaseException:
                try:
                    if not started:
                        # No submitted code ran. Remove only this invocation's named container.
                        docker(["rm", "--force", name], check=False)
                    else:
                        # Best effort freeze; never claim cancellation succeeded after an I/O error.
                        docker(["pause", name], check=False)
                except (OSError, subprocess.SubprocessError):
                    print(f"Task cleanup unavailable; inspect only {name}.", file=sys.stderr)
                # Keep the durable executing/cleanup_pending record. No automatic retry.
                raise


def main() -> int:
    """Accept one bounded administrator JSON request; expose no network listener or API keys."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path)
    parser.add_argument("--image", default="nexuspilot-task-runtime:local")
    parser.add_argument("--build-image", action="store_true")
    parser.add_argument("--enable-execution", action="store_true")
    args = parser.parse_args()
    try:
        if args.build_image:
            # Refuse a missing base before BuildKit could resolve it from a registry.
            resolve_image("nexuspilot-file-runtime:local")
            built = docker(
                [
                    "build",
                    "--pull=false",
                    "--network=none",
                    "--tag",
                    args.image,
                    str(Path(__file__).resolve().parent),
                ],
                timeout=120,
            )
            print(built.stdout.decode(errors="replace"))
            print(resolve_image(args.image))
            return 0
        if args.root is None:
            raise ValueError("An explicit private root is required")
        raw = sys.stdin.buffer.read(100000)
        if len(raw) == 100000:
            raise ValueError("Administrative request exceeds byte limit")
        request = json.loads(raw)
        store = WorkspaceStore(args.root)
        if request["action"] == "create":
            result = {
                "workspace_reference": store.create(
                    request["scope"], allow_execute=args.enable_execution
                )
            }
        elif request["action"] == "run_command":
            if not args.enable_execution:
                raise PermissionError("Task execution is disabled")
            executor = TaskExecutor(
                store, image_id=resolve_image(args.image), enabled=args.enable_execution
            )
            result = executor.execute(
                request["workspace_reference"],
                request["scope"],
                request["call_id"],
                request["command"],
            )
        else:
            raise ValueError("Unsupported administrative action")
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (
        OSError,
        ValueError,
        RuntimeError,
        KeyError,
        subprocess.SubprocessError,
    ) as error:
        print(json.dumps({"status": "failed", "error_type": type(error).__name__}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
