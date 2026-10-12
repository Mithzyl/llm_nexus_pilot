"""Verify task directory ownership and optional real local Docker code execution.

Run with NEXUSPILOT_TEST_TASK_RUNTIME=1 only after building the local file image.
These tests use synthetic files and temporary directories, never application data.
"""

import hashlib
import importlib.util
import io
import json
import os
import tarfile
import threading
import uuid
from pathlib import Path

import pytest

RUNTIME = Path(__file__).resolve().parents[3] / "deployments" / "task-runtime"
SCOPE = {"user_id": "user-a", "run_id": "run-a", "task_id": "task-a"}


@pytest.fixture
def runtime():
    """Load the administrative executor without introducing Docker access into the API."""
    spec = importlib.util.spec_from_file_location("task_runtime", RUNTIME / "task_runtime.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def store(runtime, tmp_path):
    """Create a private root and immutable input with a verified content hash."""
    return runtime.WorkspaceStore(tmp_path / "tasks")


def make_workspace(store, *, scope=None, allow_execute=True):
    """Provision a task through the trusted host boundary, not model-provided identity."""
    content = b"name,value\nalpha,7\n"
    return store.create(
        scope or SCOPE,
        inputs={"source.csv": content},
        hashes={"source.csv": hashlib.sha256(content).hexdigest()},
        allow_execute=allow_execute,
    )


@pytest.mark.parametrize("part", ["user_id", "run_id", "task_id"])
def test_workspace_requires_exact_owner_run_and_task(store, part):
    """Knowing an opaque workspace reference cannot grant another identity access."""
    reference = make_workspace(store)
    with pytest.raises(PermissionError):
        with store.locked(reference, {**SCOPE, part: "other"}):
            pytest.fail("Mismatched scope was accepted")


def test_workspace_hash_and_private_root_are_required(runtime, store, tmp_path):
    """Reject changed inputs and insecure host roots before creating a workspace."""
    with pytest.raises(ValueError, match="hash"):
        store.create(SCOPE, inputs={"x": b"changed"}, hashes={"x": "0" * 64})
    public = tmp_path / "public"
    public.mkdir(mode=0o755)
    with pytest.raises(ValueError, match="private"):
        runtime.WorkspaceStore(public)


@pytest.mark.parametrize("path", ["../x", "/etc/passwd", "a/../../b", "a//b", "a\\b", "a/./b"])
def test_workspace_rejects_ambiguous_relative_paths(runtime, path):
    """File names must not escape or alias other paths through normalization."""
    with pytest.raises(ValueError):
        runtime.relative_path(path)


def test_workspace_locks_conflicting_operations(store):
    """A second execution cannot race snapshots or output writes for the same task."""
    reference = make_workspace(store)
    with store.locked(reference, SCOPE):
        with pytest.raises(BlockingIOError):
            with store.locked(reference, SCOPE):
                pytest.fail("Concurrent workspace access was accepted")


def test_stored_snapshot_cannot_be_replaced_with_a_host_link(store, tmp_path):
    """A replaced snapshot root must not expose host files even if they fit normal file limits."""
    reference = make_workspace(store)
    external = tmp_path / "not-a-task"
    external.mkdir()
    (external / "secret.txt").write_bytes(b"must-not-read")
    with store.locked(reference, SCOPE) as (directory, state):
        snapshot = str(uuid.uuid4())
        (directory / "snapshots" / snapshot).symlink_to(external, target_is_directory=True)
        state["snapshot"] = snapshot
        store.save_state(directory, state)
    with pytest.raises(ValueError, match="snapshot"):
        store.read_file(reference, SCOPE, "secret.txt")


def test_binary_stdout_is_still_bounded_after_utf8_conversion(runtime):
    """Replacement characters must not expand retained binary output beyond the public byte cap."""
    result, truncated = runtime.output_text(b"\xff" * 32768, False)
    assert len(result.encode("utf-8")) <= 32768
    assert truncated


@pytest.mark.parametrize("member_type", [tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.FIFOTYPE])
def test_export_rejects_links_and_special_files(runtime, member_type):
    """Do not materialize model-created links or devices from container archives."""
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as archive:
        member = tarfile.TarInfo("./unsafe")
        member.type = member_type
        member.linkname = "/etc/passwd"
        archive.addfile(member)
    with pytest.raises(ValueError, match="regular"):
        runtime.decode_snapshot(output.getvalue())


def test_export_rejects_archive_traversal_and_limits(runtime):
    """Validate archive member paths and actual byte quotas before host writes."""
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as archive:
        member = tarfile.TarInfo("../escape")
        member.size = 1
        archive.addfile(member, io.BytesIO(b"x"))
    with pytest.raises(ValueError):
        runtime.decode_snapshot(output.getvalue())
    with pytest.raises(ValueError, match="limit"):
        runtime.validate_files({"large": b"x" * (runtime.MAX_FILE_BYTES + 1)})


def test_empty_directories_survive_snapshot_round_trip(runtime, store):
    """A created but empty working directory must still exist during the next task call."""
    archive_bytes = io.BytesIO()
    with tarfile.open(fileobj=archive_bytes, mode="w") as archive:
        entry = tarfile.TarInfo("./scratch")
        entry.type = tarfile.DIRTYPE
        archive.addfile(entry)
    decoded = runtime.decode_snapshot(archive_bytes.getvalue())
    reference = make_workspace(store)
    with store.locked(reference, SCOPE) as (directory, state):
        state["snapshot"] = store.save_snapshot(directory, decoded)
        restored = runtime.encode_snapshot(store.snapshot_files(directory, state))
    with tarfile.open(fileobj=io.BytesIO(restored), mode="r:") as archive:
        assert "scratch" in archive.getnames()


@pytest.mark.parametrize(
    "executable,arguments",
    [
        ("python", ["-c", "print(1)"]),
        ("shell", ["-c", "id"]),
        ("pip", []),
        ("python", ["/tmp/a.py"]),
    ],
)
def test_commands_reject_unregistered_interpreters_and_command_flags(
    runtime, executable, arguments
):
    """Only stdin or a relative script may select execution; no host command strings."""
    with pytest.raises(ValueError):
        runtime.command_arguments({"executable": executable, "arguments": arguments})


def test_execute_requires_deployment_and_task_permission(runtime, store):
    """Neither a task grant alone nor deployment enablement alone permits execution."""
    reference = make_workspace(store)
    executor = runtime.TaskExecutor(store, image_id="sha256:" + "0" * 64)
    with pytest.raises(PermissionError):
        executor.execute(reference, SCOPE, str(uuid.uuid4()), {"executable": "python"})
    denied = make_workspace(store, allow_execute=False)
    executor.enabled = True
    with pytest.raises(PermissionError):
        executor.execute(denied, SCOPE, str(uuid.uuid4()), {"executable": "python"})


@pytest.fixture
def docker_executor(runtime, store):
    """Opt in to actual Docker Desktop or Linux containers with a prebuilt local image."""
    if os.environ.get("NEXUSPILOT_TEST_TASK_RUNTIME") != "1":
        pytest.skip("Requires explicit local Docker execution and a built file-runtime image")
    image = runtime.resolve_image(
        os.environ.get("TASK_RUNTIME_TEST_IMAGE", "nexuspilot-task-runtime:local")
    )
    return runtime.TaskExecutor(store, image_id=image, enabled=True)


def run_python(executor, reference, code, **options):
    """Submit bounded Python stdin as a unique audited local command."""
    return executor.execute(
        reference, SCOPE, str(uuid.uuid4()), {"executable": "python", "stdin": code}, **options
    )


def test_real_python_shell_persistence_and_isolation(docker_executor, store):
    """Execute both interpreters, reuse files, and verify read-only inputs and task isolation."""
    reference = make_workspace(store)
    first = run_python(
        docker_executor,
        reference,
        "from pathlib import Path\np=Path('/input/source.csv')\n"
        "assert 'alpha,7' in p.read_text()\nPath('result.txt').write_text('stage-one')\n"
        "try:\n p.write_text('bad')\nexcept OSError:\n print('input-readonly')\n"
        "else:\n raise AssertionError('writable input')\n",
    )
    assert first["status"] == "completed", first
    assert first["exit_code"] == 0
    assert "input-readonly" in first["stdout"]
    call_id = str(uuid.uuid4())
    command = {
        "executable": "shell",
        "stdin": "cat result.txt\nprintf '\nstage-two' >> result.txt\n",
    }
    second = docker_executor.execute(reference, SCOPE, call_id, command)
    assert second["stdout"] == "stage-one", second
    assert store.read_file(reference, SCOPE, "result.txt") == b"stage-one\nstage-two"
    assert docker_executor.execute(reference, SCOPE, call_id, command) == second
    assert store.read_file(reference, SCOPE, "result.txt") == b"stage-one\nstage-two"
    with pytest.raises(ValueError, match="different"):
        docker_executor.execute(reference, SCOPE, call_id, {"executable": "python"})
    other = make_workspace(store, scope={**SCOPE, "task_id": "task-b"})
    isolated = docker_executor.execute(
        other,
        {**SCOPE, "task_id": "task-b"},
        str(uuid.uuid4()),
        {"executable": "shell", "stdin": "test ! -e result.txt && test ! -e /var/run/docker.sock"},
    )
    assert isolated["exit_code"] == 0


def test_real_environment_network_and_output_bounds(docker_executor, store, monkeypatch):
    """No application secrets/network; stdout and stderr are drained together with byte caps."""
    monkeypatch.setenv("NEXUSPILOT_DEEPSEEK_API_KEY", "synthetic-not-a-real-key")
    reference = make_workspace(store)
    result = run_python(
        docker_executor,
        reference,
        "import os,socket,sys\nassert 'NEXUSPILOT_DEEPSEEK_API_KEY' not in os.environ\n"
        "assert os.getuid()==65532\nimport importlib.util,sysconfig\n"
        "assert importlib.util.find_spec('pip') is None\n"
        "assert importlib.util.find_spec('ensurepip') is None\n"
        "import docx,openpyxl,pptx\n"
        "try:\n open(sysconfig.get_paths()['purelib']+'/installed.py','w')\n"
        "except OSError:\n pass\nelse:\n raise AssertionError('writable environment')\n"
        "s=socket.socket();s.settimeout(0.3)\n"
        "try:\n s.connect(('1.1.1.1',443))\nexcept OSError:\n pass\n"
        "else:\n raise AssertionError('network available')\n"
        "for i in range(1000):\n os.write(1,b'x'*1024);os.write(2,b'y'*1024)\n"
        "sys.exit(7)\n",
    )
    assert result["exit_code"] == 7
    assert result["status"] == "failed"
    assert result["stdout_truncated"] and result["stderr_truncated"]
    assert len(result["stdout"].encode()) <= 32768
    assert len(result["stderr"].encode()) <= 32768


@pytest.mark.parametrize("termination", ["timeout", "cancel"])
def test_real_termination_stops_children_before_snapshot(docker_executor, store, termination):
    """Stop the container and keep the last saved snapshot, never publish partial writes."""
    reference = make_workspace(store)
    seed = run_python(docker_executor, reference, "open('stable.txt','w').write('saved')")
    assert seed["status"] == "completed"
    event = threading.Event()
    timer = threading.Timer(1, event.set) if termination == "cancel" else None
    if timer:
        timer.start()
    try:
        result = run_python(
            docker_executor,
            reference,
            "import subprocess,time\nopen('stable.txt','w').write('partial')\n"
            "subprocess.Popen(['sleep','30'])\ntime.sleep(30)\n",
            timeout_seconds=2,
            cancel=event,
        )
    finally:
        if timer:
            timer.join()
    assert result["status"] == ("cancelled" if termination == "cancel" else "timed_out")
    assert result["container_removed"] is True
    assert store.read_file(reference, SCOPE, "stable.txt") == b"saved"


def test_real_saved_script_and_relative_working_directory(docker_executor, store):
    """A later call can run an explicitly selected script with arguments inside a task subfolder."""
    reference = make_workspace(store)
    first = run_python(
        docker_executor,
        reference,
        "from pathlib import Path\nPath('scripts').mkdir()\n"
        "Path('scripts/hello.py').write_text('import sys;print(sys.argv[1])')\n",
    )
    assert first["status"] == "completed", first
    second = docker_executor.execute(
        reference,
        SCOPE,
        str(uuid.uuid4()),
        {"executable": "python", "arguments": ["hello.py", "hello-task"], "cwd": "scripts"},
    )
    assert second["stdout"] == "hello-task\n", second


def test_real_background_processes_are_stopped_without_publishing_partial_files(
    docker_executor, store
):
    """Disallow detached jobs rather than racing file export against a still-running child."""
    reference = make_workspace(store)
    result = run_python(
        docker_executor,
        reference,
        "import subprocess\nfrom pathlib import Path\nPath('partial').write_text('x')\n"
        "subprocess.Popen(['sleep','30'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)\n",
    )
    assert result["error_code"] == "background_processes_not_supported", result
    assert result["container_removed"]
    with pytest.raises(FileNotFoundError):
        store.read_file(reference, SCOPE, "partial")


def test_real_output_byte_quota_rejects_oversized_file(docker_executor, store):
    """A model-created file over the per-file quota is never published as a task snapshot."""
    reference = make_workspace(store)
    result = run_python(
        docker_executor,
        reference,
        "from pathlib import Path\nPath('large').write_bytes(b'x'*(9*1024*1024))",
    )
    assert result["error_code"] == "invalid_output_files", result
    with pytest.raises(FileNotFoundError):
        store.read_file(reference, SCOPE, "large")


def test_real_failed_snapshot_save_retains_container_and_blocks_reexecution(
    runtime,
    docker_executor,
    store,
    monkeypatch,
):
    """A failed host write retains the sole output and survives executor object recreation."""
    reference = make_workspace(store)

    def unavailable_storage(directory, files):
        """Fail at the persistence boundary after reading actual container output."""
        assert files["unique.txt"] == b"only-copy"
        raise OSError("Synthetic disk failure")

    monkeypatch.setattr(store, "save_snapshot", unavailable_storage)
    with pytest.raises(OSError, match="Synthetic disk"):
        run_python(docker_executor, reference, "open('unique.txt','w').write('only-copy')")
    state = json.loads((store.root / reference / "state.json").read_bytes())
    name = state["container"]
    try:
        paused = runtime.docker(["inspect", "--format", "{{.State.Paused}}", name]).stdout
        assert paused.strip() == b"true"
        fresh = runtime.TaskExecutor(
            runtime.WorkspaceStore(store.root), image_id=docker_executor.image_id, enabled=True
        )
        with pytest.raises(RuntimeError, match="recovery"):
            run_python(fresh, reference, "print('must not execute')")
    finally:
        # This is synthetic test output, deliberately removed only after retention was verified.
        runtime.docker(["rm", "--force", name])


@pytest.mark.parametrize(
    "code",
    [
        "import os\nos.symlink('/etc/passwd','stolen')",
        "import os\nopen('source','w').write('x')\nos.link('source','stolen')",
        "import os\nos.mkfifo('pipe')",
    ],
)
def test_real_link_export_is_rejected(docker_executor, store, code):
    """A successful interpreter exit cannot publish links or special files as valid output."""
    reference = make_workspace(store)
    result = run_python(docker_executor, reference, code)
    assert result["status"] == "failed"
    assert result["error_code"] == "invalid_output_files"
    with pytest.raises(FileNotFoundError):
        store.read_file(reference, SCOPE, "stolen")
