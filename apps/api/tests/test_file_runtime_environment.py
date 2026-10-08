"""Check the trusted file-runtime launcher without Docker or infrastructure writes.

Real dependency and Linux restriction checks run separately with
``make file-runtime-check`` after ``make file-runtime-build``.
"""

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

RUNTIME_DIRECTORY = Path(__file__).resolve().parents[3] / "deployments" / "file-runtime"


@pytest.fixture
def launcher():
    """Load the standalone deployment command without importing API configuration or secrets."""

    spec = importlib.util.spec_from_file_location(
        "file_runtime_launcher", RUNTIME_DIRECTORY / "manage_runtime.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_container_configuration_denies_network_privileges_and_host_mounts(launcher):
    """Only the fixed acceptance entrypoint runs with a bounded writable tmpfs."""

    arguments = launcher.container_create_arguments("test-container", "sha256:" + "a" * 64)
    for option, expected in (
        ("--network", "none"),
        ("--user", "65532:65532"),
        ("--cap-drop", "ALL"),
        ("--security-opt", "no-new-privileges:true"),
        ("--memory", "512m"),
        ("--memory-swap", "512m"),
        ("--cpus", "1"),
        ("--pids-limit", "64"),
    ):
        assert arguments[arguments.index(option) + 1] == expected
    assert "--read-only" in arguments and "--init" in arguments
    assert not {"--privileged", "--env-file", "--env", "--volume", "--mount", "--entrypoint"} & set(
        arguments
    )
    assert "noexec,nosuid,nodev" in arguments[arguments.index("--tmpfs") + 1]
    assert arguments[-1] == "sha256:" + "a" * 64


def test_failed_or_timed_out_check_removes_only_its_own_container(launcher, monkeypatch):
    """A failed runtime never remains active and cleanup cannot target another task."""

    calls = []

    def fake_run(arguments, **kwargs):
        """Simulate Docker starting a task and timing out without real daemon calls."""

        calls.append(arguments)
        if arguments[1:3] == ["image", "inspect"]:
            return subprocess.CompletedProcess(arguments, 0, "sha256:" + "a" * 64 + "\n", "")
        if arguments[1] == "start":
            raise subprocess.TimeoutExpired(arguments, 120)
        return subprocess.CompletedProcess(arguments, 0, "", "")

    monkeypatch.setattr(launcher.subprocess, "run", fake_run)
    assert launcher.check_runtime() == 1
    created = next(arguments for arguments in calls if arguments[1] == "create")
    name = created[created.index("--name") + 1]
    assert name.startswith("nexuspilot-file-check-")
    assert calls[-1] == ["docker", "rm", "--force", name]


def test_missing_image_does_not_pull_or_start_a_container(launcher, monkeypatch):
    """Runtime preparation is explicit, never triggered by a file task or failed check."""

    calls = []

    def missing_image(arguments, **kwargs):
        """Report a missing local image without accessing a registry."""

        calls.append(arguments)
        raise subprocess.CalledProcessError(1, arguments, stderr="No such image")

    monkeypatch.setattr(launcher.subprocess, "run", missing_image)
    assert launcher.check_runtime() == 1
    assert len(calls) == 1
    assert calls[0][1:3] == ["image", "inspect"]


def test_build_context_excludes_application_and_user_files():
    """The build receives only its definition, pins, parser and acceptance script."""

    patterns = (RUNTIME_DIRECTORY / ".dockerignore").read_text().splitlines()
    assert patterns == [
        "*",
        "!Dockerfile",
        "!requirements.txt",
        "!check_runtime.py",
        "!parse_office.py",
        "!parse_pdf.py",
    ]
    dockerfile = (RUNTIME_DIRECTORY / "Dockerfile").read_text()
    assert "USER 65532:65532" in dockerfile
    assert "COPY apps" not in dockerfile
    assert "COPY . " not in dockerfile


@pytest.mark.parametrize(
    ("scenario", "expected_exit"),
    [
        ("success", 0),
        ("missing_check", 1),
        ("failed_check", 1),
        ("process_failed", 1),
        ("invalid_json", 1),
        ("cleanup_failed", 1),
    ],
)
def test_acceptance_requires_every_check_process_and_cleanup_to_succeed(
    launcher, monkeypatch, scenario, expected_exit
):
    """Reject incomplete or contradictory reports and failed cleanup, even after valid JSON."""

    report = {
        "status": "passed",
        "checks": {
            name: {"status": "passed"}
            for name in ("isolation", "docx", "xlsx", "pptx", "pdf", "office_parser")
        },
    }
    if scenario == "missing_check":
        del report["checks"]["pdf"]
    if scenario == "failed_check":
        report["checks"]["isolation"]["status"] = "failed"
    calls = []

    def run_docker(arguments, **kwargs):
        """Provide synthetic command results without contacting a Docker daemon."""

        calls.append(arguments)
        if arguments[1:3] == ["image", "inspect"]:
            return subprocess.CompletedProcess(arguments, 0, "sha256:" + "a" * 64, "")
        if arguments[1] == "start":
            stdout = "not JSON" if scenario == "invalid_json" else json.dumps(report)
            return subprocess.CompletedProcess(arguments, int(scenario == "process_failed"), stdout)
        if arguments[1] == "rm":
            return subprocess.CompletedProcess(arguments, int(scenario == "cleanup_failed"), "")
        return subprocess.CompletedProcess(arguments, 0, "")

    monkeypatch.setattr(launcher.subprocess, "run", run_docker)
    assert launcher.check_runtime() == expected_exit
    assert calls[-1][1:3] == ["rm", "--force"]
