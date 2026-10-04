"""Check provisioning starts in a fresh process, as it does in a deployment container."""

import os
import subprocess
import sys
from pathlib import Path


def test_provision_command_help_does_not_require_prior_app_import():
    """Load account provisioning in a fresh process without circular imports or DB access."""
    root = Path(__file__).resolve().parents[3]
    result = subprocess.run(
        [sys.executable, "-m", "nexuspilot_api.commands.provision_local_user", "--help"],
        env={
            **os.environ,
            "PYTHONPATH": f"{root / 'apps/api/src'}{os.pathsep}{root / 'packages/models/src'}",
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "--user-id" in result.stdout
