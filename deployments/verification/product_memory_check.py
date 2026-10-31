"""Run product-memory tests against existing loopback MySQL/MinIO containers.

From repository root: apps/api/.venv/bin/python deployments/verification/product_memory_check.py
Requires running compose-mysql-1 and compose-minio-1 plus an authz_audit MySQL
management database. Credentials are read in process from container configuration,
never printed. Each test uses its own disposable authz_audit_* database; MinIO
uses the authorization-audit test bucket. No production data, model calls, hashes,
automatic object deletion, API restart or Git operations are introduced here.
Use --linked-sharing for the focused cross-owner sharing check; that mode retains
its generated database and prints only its name, not credentials or memory text.
Use --account-link-concurrency for three isolated MySQL confirmation races; this
mode also retains generated databases and never invokes a model or real App.
--mcp-revocation and --mcp-catalog each run one fixed-model workflow with real
MySQL and a simulated MCP invocation; both retain their verification databases.
"""

import argparse
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
    parser = argparse.ArgumentParser()
    parser.add_argument("--linked-sharing", action="store_true",
                        help="Run only explicit account-linked sharing and retain its database")
    parser.add_argument("--account-link-concurrency", action="store_true",
                        help="Run only real MySQL account-link races and retain their databases")
    parser.add_argument("--mcp-revocation", action="store_true",
                        help="Verify in-flight MCP revocation with MySQL and a fixed model")
    parser.add_argument("--mcp-catalog", action="store_true",
                        help="Verify independent MCP catalog invocation with MySQL and a fixed model")
    parser.add_argument("--linked-instructions", action="store_true",
                        help="Verify linked instruction consent with MySQL/MinIO and retain the database")
    parser.add_argument("--app-prompts", action="store_true",
                        help="Verify App endpoints through fixed-model prompt assembly with MySQL/MinIO")
    parser.add_argument("--password-reset", action="store_true",
                        help="Verify password recovery with MySQL and synthetic mail delivery")
    parser.add_argument("--app-scope", action="store_true",
                        help="Verify same-owner cross-App Run isolation with MySQL and no model calls")
    args = parser.parse_args()
    if sum((args.linked_sharing, args.account_link_concurrency, args.mcp_revocation, args.mcp_catalog, args.linked_instructions, args.app_prompts, args.password_reset, args.app_scope)) > 1:
        parser.error("Choose one focused verification mode")
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
    target = "tests/test_product_memory.py"
    if args.linked_sharing:
        target = "tests/test_linked_memory_sharing.py"
        environment["NEXUSPILOT_TEST_AUTH_KEEP_DATABASE"] = "true"
    if args.account_link_concurrency:
        if args.linked_sharing:
            parser.error("Choose one focused verification mode")
        target = "tests/test_account_linking_concurrency.py"
        environment["NEXUSPILOT_TEST_AUTH_KEEP_DATABASE"] = "true"
    if args.mcp_revocation:
        target = "tests/test_task_extensions.py::test_mcp_in_general_agent[user_disabled]"
        environment["NEXUSPILOT_TEST_AUTH_KEEP_DATABASE"] = "true"
    if args.mcp_catalog:
        target = "tests/test_task_extensions.py::test_mcp_in_general_agent[independent]"
        environment["NEXUSPILOT_TEST_AUTH_KEEP_DATABASE"] = "true"
    if args.linked_instructions:
        target = "tests/test_linked_user_instructions.py"
        environment["NEXUSPILOT_TEST_AUTH_KEEP_DATABASE"] = "true"
    if args.app_prompts:
        target = "tests/test_application_prompt_injection.py"
        environment["NEXUSPILOT_TEST_AUTH_KEEP_DATABASE"] = "true"
    if args.password_reset:
        target = "tests/test_password_reset.py"
        environment["NEXUSPILOT_TEST_AUTH_KEEP_DATABASE"] = "true"
    if args.app_scope:
        target = ["tests/test_application_scope.py", "tests/test_application_diagnostics.py"]
        environment["NEXUSPILOT_TEST_AUTH_KEEP_DATABASE"] = "true"
    return subprocess.call(
        [sys.executable, "-m", "pytest", *([target] if isinstance(target, str) else target),
         "-q", "-s", "--tb=short"],
        cwd=root / "apps/api",
        env=environment,
    )


if __name__ == "__main__":
    raise SystemExit(main())
