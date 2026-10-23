"""Catch startup import cycles hidden by the main test process's preloaded modules."""

import os
import subprocess
import sys
from pathlib import Path


def test_main_imports_in_fresh_process():
    """Load the actual uvicorn entry point without fixture imports or network startup."""
    root = Path(__file__).resolve().parents[3]
    result = subprocess.run(
        [sys.executable, "-c", "from nexuspilot_api.main import app; assert app.routes"],
        cwd=root,
        env={**os.environ, "PYTHONPATH": "apps/api/src:packages/models/src"},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
