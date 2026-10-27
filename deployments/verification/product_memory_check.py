"""Run product-memory tests against existing loopback MySQL/MinIO containers.

From repository root: apps/api/.venv/bin/python deployments/verification/product_memory_check.py
Requires running compose-mysql-1 and compose-minio-1 plus an authz_audit MySQL
management database. Credentials are read in process from container configuration,
never printed. Each test uses its own disposable authz_audit_* database; MinIO
uses the authorization-audit test bucket. No production data, model calls, hashes,
automatic object deletion, API restart or Git operations are introduced here.
"""

import json
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote


def container_environment(name):
    """Read only one named local container's environment without printing its credentials."""
    record = json.loads(subprocess.check_output(["docker", "inspect", name], text=True))[0]
    return dict(item.split("=", 1) for item in record["Config"]["Env"] if "=" in item)


def main():
    """Execute the same regression cases with real persistence and fixed model responses."""
    root = Path(__file__).resolve().parents[2]
    mysql = container_environment("compose-mysql-1")
    minio = container_environment("compose-minio-1")
    environment = dict(os.environ)
    environment.update(
        NEXUSPILOT_TEST_AUTH_MYSQL_URL=(
            "mysql+aiomysql://root:"
            + quote(mysql["MYSQL_ROOT_PASSWORD"], safe="")
            + "@127.0.0.1:3306/authz_audit"
        ),
        NEXUSPILOT_TEST_AUTH_MINIO_ENDPOINT="127.0.0.1:9000",
        NEXUSPILOT_TEST_AUTH_MINIO_ACCESS_KEY=minio["MINIO_ROOT_USER"],
        NEXUSPILOT_TEST_AUTH_MINIO_SECRET_KEY=minio["MINIO_ROOT_PASSWORD"],
    )
    return subprocess.call(
        [sys.executable, "-m", "pytest", "tests/test_product_memory.py", "-q", "--tb=short"],
        cwd=root / "apps/api",
        env=environment,
    )


if __name__ == "__main__":
    raise SystemExit(main())
