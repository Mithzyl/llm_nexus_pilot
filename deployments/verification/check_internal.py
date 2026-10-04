"""Verify direct API authorization and container permissions in the disposable stack.

Execute inside its API container with synthetic session cookies on stdin. Never
print cookies, service credentials or private responses. This helper is mounted
only by the verification overlay, not included in the production image.
"""

import asyncio
import json
import os
import pathlib
import subprocess
import sys
import tempfile

import httpx
from minio.error import S3Error
from sqlalchemy import text

import nexuspilot_api.models  # noqa: F401
from nexuspilot_api.core.config import get_settings
from nexuspilot_api.infrastructure.database import database_session_factory, engine
from nexuspilot_api.infrastructure.object_storage import ObjectStorage
from nexuspilot_api.infrastructure.pdf_text_extraction import _sandbox_command


async def main(payload):
    """Require direct-access denials and inspect actual least-privilege backing accounts."""
    settings = get_settings()
    report = {}
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1:8000", timeout=10
    ) as client:
        route = f"/api/v1/sessions/{payload['session_id']}"
        assert (await client.get(route)).status_code == 401
        client.headers["X-API-Key"] = settings.api_key
        assert (await client.get(route)).status_code == 401
        client.headers.update(
            {
                "Cookie": payload["cookie"],
                "X-CSRF-Token": payload["csrf"],
                "Origin": next(iter(settings.allowed_authentication_origins())),
            }
        )
        assert (await client.get("/api/v1/auth/me")).json()["user_id"] == "preverify-b"
        assert (await client.get(route)).status_code == 404
        context = {
            "user_id": "preverify-a",
            "session_id": payload["session_id"],
            "provider": "deepseek",
            "model": "deepseek-v4-flash",
            "token_budget": 8192,
        }
        for media in (
            None,
            "application/json",
            "Application/JSON",
            "application/vnd.api+json",
            "application/json; charset=utf-8",
            "APPLICATION/PROBLEM+JSON",
        ):
            response = await client.post(
                "/api/v1/context-builds/preview",
                headers={"Content-Type": media} if media else {},
                content=json.dumps(context),
            )
            assert response.status_code == 404, (media, response.status_code)
        response = await client.post(
            "/api/v1/users",
            headers={
                "X-Internal-API-Key": settings.internal_api_key.get_secret_value()
            },
            json={"user_id": "forbidden-test-user", "display_name": "forbidden"},
        )
        assert response.status_code == 403
    report["direct_api_anonymous_and_service_only"] = "passed"
    report["direct_api_cross_user_and_json_variants"] = "passed"
    report["browser_cannot_elevate_with_internal_key"] = "passed"
    assert os.geteuid() == 10001
    report["api_nonroot"] = "passed"
    async with database_session_factory() as db:
        assert (
            await db.execute(text("SELECT version_num FROM alembic_version"))
        ).scalar() == "20260916_0022"
        grants = [row[0] for row in (await db.execute(text("SHOW GRANTS"))).all()]
        assert not any(
            word in grant
            for grant in grants
            for word in ("ALL PRIVILEGES", "CREATE", "DROP", "ALTER", "GRANT OPTION")
        )
    await engine.dispose()
    report["migration_and_database_dml_permissions"] = "passed"
    storage = ObjectStorage()
    assert all(
        bucket.name == settings.minio_bucket for bucket in storage.client.list_buckets()
    )
    try:
        storage.client.make_bucket("preverify-disallowed-bucket")
    except S3Error as exc:
        assert exc.code == "AccessDenied"
    else:
        raise AssertionError("Unexpected bucket-creation permission")
    report["storage_bucket_scope"] = "passed"
    with tempfile.TemporaryDirectory() as directory:
        result = subprocess.run(
            _sandbox_command(pathlib.Path(directory), ["/usr/bin/true"]),
            capture_output=True,
            text=True,
        )
        report["pdf_sandbox"] = {
            "status": "passed" if result.returncode == 0 else "failed",
            "exit_code": result.returncode,
            "diagnostic": result.stderr.strip()[:300],
        }
    print(json.dumps(report))


if __name__ == "__main__":
    asyncio.run(main(json.load(sys.stdin)))
