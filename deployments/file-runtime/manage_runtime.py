"""Build or verify the trusted, preinstalled file environment on the local Docker daemon.

Usage: ``make file-runtime-build`` then ``make file-runtime-check``.
Build downloads dependencies. Check never pulls an image, inherits no application
configuration into the container, and removes only its uniquely named container.
This administrative command is not an HTTP endpoint or a model-callable tool.
"""

import argparse
import json
import re
import subprocess
import sys
import uuid
from pathlib import Path

IMAGE_TAG = "nexuspilot-file-runtime:local"
RUNTIME_DIRECTORY = Path(__file__).resolve().parent


def container_create_arguments(name: str, image_id: str) -> list[str]:
    """Create a fixed-entrypoint container with no host mounts, network or extra privileges."""

    return [
        "docker",
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
        "512m",
        "--memory-swap",
        "512m",
        "--cpus",
        "1",
        "--pids-limit",
        "64",
        "--ulimit",
        "nofile=128:128",
        "--ulimit",
        "core=0:0",
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,nodev,size=268435456,mode=1777",
        image_id,
    ]


def build_runtime(image_tag: str = IMAGE_TAG, platform: str | None = None) -> int:
    """Explicitly download and build only the dedicated runtime directory, without user data."""

    arguments = ["docker", "build", "--tag", image_tag]
    if platform:
        arguments.extend(["--platform", platform])
    try:
        return subprocess.run(
            [*arguments, str(RUNTIME_DIRECTORY)],
            check=False,
        ).returncode
    except (OSError, KeyboardInterrupt):
        print("File runtime build was unavailable or interrupted.", file=sys.stderr)
        return 1


def check_runtime(image_tag: str = IMAGE_TAG) -> int:
    """Verify an already-built image and clean up its container on success, failure or timeout."""

    try:
        image_id = subprocess.run(
            ["docker", "image", "inspect", "--format", "{{.Id}}", image_tag],
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        ).stdout.strip()
        if re.fullmatch(r"sha256:[0-9a-f]{64}", image_id) is None:
            raise ValueError("Docker returned an invalid image identity")
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(
            f"File runtime image unavailable ({type(error).__name__}); "
            "run make file-runtime-build first.",
            file=sys.stderr,
        )
        return 1

    name = f"nexuspilot-file-check-{uuid.uuid4().hex}"
    exit_code = 1
    try:
        subprocess.run(
            container_create_arguments(name, image_id),
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        checked = subprocess.run(
            ["docker", "start", "--attach", name],
            check=False,
            capture_output=True,
            text=True,
            timeout=120,
        )
        # Docker's attached process exit status and the structured report must both
        # agree. No success based merely on container creation or dependency imports.
        report = json.loads(checked.stdout)
        report["image_id"] = image_id
        report["image_tag"] = image_tag
        print(json.dumps(report, ensure_ascii=False, indent=2))
        expected = {"isolation", "docx", "xlsx", "pptx", "pdf", "office_parser"}
        checks = report.get("checks", {})
        if (
            checked.returncode == 0
            and report.get("status") == "passed"
            and set(checks) == expected
            and all(item.get("status") == "passed" for item in checks.values())
        ):
            exit_code = 0
    except (
        OSError,
        ValueError,
        TypeError,
        AttributeError,
        subprocess.SubprocessError,
        KeyboardInterrupt,
    ) as error:
        print(f"File runtime check failed ({type(error).__name__}).", file=sys.stderr)
    finally:
        try:
            cleanup = subprocess.run(
                ["docker", "rm", "--force", name],
                check=False,
                capture_output=True,
                text=True,
                timeout=15,
            )
            if cleanup.returncode:
                print(f"Container cleanup failed; inspect only {name}.", file=sys.stderr)
                exit_code = 1
        except (OSError, subprocess.SubprocessError):
            print(f"Container cleanup unavailable; inspect only {name}.", file=sys.stderr)
            exit_code = 1
    return exit_code


def main() -> int:
    """Expose only explicit build and acceptance commands, never arbitrary task commands."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("build", "check"))
    parser.add_argument("--image", default=IMAGE_TAG)
    parser.add_argument("--platform", choices=("linux/amd64", "linux/arm64"))
    arguments = parser.parse_args()
    if arguments.action == "build":
        return build_runtime(arguments.image, arguments.platform)
    return check_runtime(arguments.image)


if __name__ == "__main__":
    raise SystemExit(main())
