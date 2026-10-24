"""Opt-in real local MySQL/MinIO project validation, without any model invocation.

Run from apps/api with PYTHONPATH=src .venv/bin/python tests/manual_project_workspace_validation.py
prepare|verify|disable. Requires migrated local settings and an authenticated API at 127.0.0.1:8002.
Prepare writes synthetic accounts and fixtures, plus private browser states under ignored
deployments/data. Use the actual browser project page between prepare and verify. No user data is
deleted; disable revokes only these generated accounts. The public report contains no credentials.
"""

import argparse
import asyncio
import hashlib
import json
import os
import secrets
from pathlib import Path
from uuid import uuid4

import httpx
from sqlalchemy import func, select, text, update
from sqlalchemy.engine import make_url

from nexuspilot_api import (
    models,  # noqa: F401 -- initialize the ORM registry before feature services
)
from nexuspilot_api.core.config import get_settings
from nexuspilot_api.features.identity.services.authentication_service import (
    provision_local_password_user,
)
from nexuspilot_api.infrastructure.database import database_session_factory, engine
from nexuspilot_api.infrastructure.object_storage import get_object_storage
from nexuspilot_api.models import LlmModelAttempt, LlmRun, ProjectFile, User, UserLoginSession
from nexuspilot_api.models.base import utc_now

ROOT = Path(__file__).resolve().parents[3]
PRIVATE = ROOT / "deployments/data/project-workspace-validation"
MANIFEST = PRIVATE / "manifest.json"
REPORT = ROOT / "output/security/project-workspace-real-20261024.json"
API = "http://127.0.0.1:8002"
ORIGIN = "http://127.0.0.1:3100"
UPLOAD = b"Synthetic project workspace upload. No personal information.\n"
ARTIFACT = b"label,value\nsynthetic,42\n"


def private_json(path, value):
    """Persist generated validation secrets only in an ignored, owner-only directory."""
    PRIVATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(PRIVATE, 0o700)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        json.dump(value, stream)


def checked(response, expected=200):
    """Check status without echoing request headers, credentials or private response bodies."""
    if response.status_code != expected:
        raise RuntimeError(
            f"Unexpected status {response.status_code} at {response.request.url.path}"
        )
    return response.json() if response.content else None


async def login(client, account):
    """Authenticate the synthetic user over HTTP and bind mutation CSRF to its real cookie."""
    checked(
        await client.post(
            "/api/v1/auth/login",
            json={
                "email": account["email"],
                "password": account["password"],
                "device_name": "project-validation",
            },
        )
    )
    client.headers["X-CSRF-Token"] = client.cookies["nexuspilot_csrf"]


def browser_state(client):
    """Export real login cookies for CLI state-load without printing their contents."""
    return {
        "cookies": [
            {
                "name": cookie.name,
                "value": cookie.value,
                "domain": "127.0.0.1",
                "path": "/",
                "expires": cookie.expires or -1,
                "httpOnly": cookie.name == "nexuspilot_session",
                "secure": False,
                "sameSite": "Lax",
            }
            for cookie in client.cookies.jar
        ],
        "origins": [],
    }


async def prepare(settings):
    """Provision two unique test owners and real upload/artifact sources; never invoke a model."""
    if MANIFEST.exists():
        raise RuntimeError(
            "Validation manifest already exists; reuse it rather than duplicate accounts"
        )
    suffix = uuid4().hex[:10]
    accounts = [
        {
            "user_id": f"workspace-check-{suffix}-{letter}",
            "email": f"workspace-{suffix}-{letter}@example.invalid",
            "password": secrets.token_urlsafe(30),
        }
        for letter in ("a", "b")
    ]
    async with database_session_factory() as db:
        assert await db.scalar(text("SELECT version_num FROM alembic_version")) == "20261024_0029"
        for index, account in enumerate(accounts):
            await provision_local_password_user(
                db, **account, display_name=f"Workspace validation {index + 1}"
            )
    manifest = {"accounts": accounts}
    private_json(MANIFEST, manifest)
    async with httpx.AsyncClient(
        base_url=API,
        headers={"X-API-Key": settings.api_key, "Origin": ORIGIN},
        timeout=30,
        trust_env=False,
    ) as client:
        await login(client, accounts[0])
        upload = checked(
            await client.post(
                "/api/v1/files",
                json={
                    "filename": "workspace-check.txt",
                    "size_bytes": len(UPLOAD),
                    "content_hash": hashlib.sha256(UPLOAD).hexdigest(),
                    "idempotency_key": f"upload-{suffix}",
                },
            ),
            201,
        )
        checked(
            await client.put(
                f"/api/v1/files/{upload['file_id']}/content",
                content=UPLOAD,
                headers={"Content-Type": "application/octet-stream"},
            )
        )
        run = checked(
            await client.post(
                "/api/v1/runs",
                json={
                    "user_id": accounts[0]["user_id"],
                    "user_request": "Synthetic artifact fixture; no model request",
                },
            ),
            201,
        )
        artifact = checked(
            await client.post(
                f"/api/v1/runs/{run['run_id']}/artifacts",
                data={"artifact_type": "validation"},
                files={"file": ("workspace-result.csv", ARTIFACT, "text/csv")},
            ),
            201,
        )
        manifest.update(
            file_id=upload["file_id"], artifact_id=artifact["artifact_id"], run_id=run["run_id"]
        )
        private_json(PRIVATE / "browser-a.json", browser_state(client))
        client.cookies.clear()
        await login(client, accounts[1])
        private_json(PRIVATE / "browser-b.json", browser_state(client))
    private_json(MANIFEST, manifest)
    print("Prepared two synthetic accounts and actual MinIO sources; private browser states saved.")


async def verify(settings):
    """Verify saved copies through API, SQL, MinIO, local bytes and the other user."""
    manifest = json.loads(MANIFEST.read_text())
    accounts = manifest["accounts"]
    report = {
        "database": "local MySQL",
        "objects": "local MinIO",
        "migration": "20261024_0029",
        "checks": [],
    }
    async with httpx.AsyncClient(
        base_url=API,
        headers={"X-API-Key": settings.api_key, "Origin": ORIGIN},
        timeout=30,
        trust_env=False,
    ) as client:
        await login(client, accounts[0])
        projects = checked(await client.get("/api/v1/projects"))["items"]
        assert len(projects) == 1 and projects[0]["memory_status"] == "disabled"
        project_id = projects[0]["project_id"]
        files = checked(await client.get(f"/api/v1/projects/{project_id}/files"))
        assert len(files) == 2 and all(item["status"] == "ready" for item in files)
        assert {item["source_type"] for item in files} == {"file", "artifact"}
        assert (ROOT / ".playwright-cli/workspace-check.txt").read_bytes() == UPLOAD
        assert (ROOT / ".playwright-cli/workspace-result.csv").read_bytes() == ARTIFACT
        report["browser_downloads_match"] = True
        for entry in files:
            expected = UPLOAD if entry["source_type"] == "file" else ARTIFACT
            response = await client.get(
                f"/api/v1/projects/{project_id}/files/{entry['project_file_id']}/content"
            )
            assert response.status_code == 200 and response.content == expected
            assert "attachment" in response.headers["content-disposition"]
            async with database_session_factory() as db:
                row = await db.get(ProjectFile, entry["project_file_id"])
                obj = await get_object_storage().open_object(row.storage_uri)
                assert await asyncio.to_thread(b"".join, obj.chunks) == expected
                assert row.storage_uri == get_object_storage().uri_for_object(
                    f"project-files/{project_id}/{entry['project_file_id']}"
                )
            local = Path(settings.project_workspace_root) / project_id / entry["project_file_id"]
            assert local.read_bytes() == expected and local.stat().st_mode & 0o777 == 0o400
            report["checks"].append(
                {
                    "project_file_id": entry["project_file_id"],
                    "source_type": entry["source_type"],
                    "download": True,
                    "minio_bytes": True,
                    "local_readonly_copy": True,
                }
            )
        # Repeated save and conflicting input are checked against the same actual database row.
        one = files[0]
        conflict = await client.post(
            f"/api/v1/projects/{project_id}/files",
            json={
                "file_id": manifest["file_id"],
                "filename": one["filename"],
                "idempotency_key": "conflict-verification",
            },
        )
        assert conflict.status_code == 409
        checked(await client.post(f"/api/v1/runs/{manifest['run_id']}/cancel"))
        client.cookies.clear()
        await login(client, accounts[1])
        assert checked(await client.get("/api/v1/projects"))["items"] == []
        denials = []
        for method, path, body in [
            ("GET", f"/projects/{project_id}", None),
            ("GET", f"/projects/{project_id}/files", None),
            ("PATCH", f"/projects/{project_id}", {"project_name": "blocked"}),
            ("GET", f"/projects/{project_id}/sessions", None),
            ("POST", f"/projects/{project_id}/sessions", {"idempotency_key": "blocked-session"}),
        ] + [
            (method, f"/projects/{project_id}/files/{entry['project_file_id']}/{action}", None)
            for entry in files
            for method, action in [("GET", "content"), ("POST", "materialize")]
        ]:
            response = await client.request(method, "/api/v1" + path, json=body)
            assert response.status_code == 404
            denials.append({"method": method, "path": path, "status": response.status_code})
        report.update(
            project_id=project_id,
            cross_user_denials=denials,
            project_memory="disabled",
            filename_conflict=409,
        )
    async with database_session_factory() as db:
        report["model_attempt_count"] = await db.scalar(
            select(func.count())
            .select_from(LlmModelAttempt)
            .join(LlmRun, LlmRun.run_id == LlmModelAttempt.run_id)
            .where(LlmRun.user_id.in_([a["user_id"] for a in accounts]))
        )
        assert report["model_attempt_count"] == 0
    report["browser"] = "CLI verification recorded separately; no mocked HTTP responses"
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {
                "report": str(REPORT),
                "files_verified": len(files),
                "cross_user_denials": len(denials),
                "model_attempt_count": 0,
            }
        )
    )


async def disable():
    """Revoke only the generated test owners and sessions, retaining all files and audit records."""
    manifest = json.loads(MANIFEST.read_text())
    identities = [a["user_id"] for a in manifest["accounts"]]
    async with database_session_factory() as db:
        await db.execute(update(User).where(User.user_id.in_(identities)).values(is_active=False))
        await db.execute(
            update(UserLoginSession)
            .where(UserLoginSession.user_id.in_(identities))
            .values(revoked_at=utc_now())
        )
        await db.commit()
    print("Disabled generated validation accounts and revoked sessions; no data deleted.")


async def main(action):
    """Refuse nonlocal infrastructure and dispose database connections on every exit."""
    settings = get_settings()
    assert make_url(settings.database_url).host in {"localhost", "127.0.0.1"}
    assert settings.minio_endpoint.split(":")[0] in {"localhost", "127.0.0.1"}
    assert settings.workflow_execution_backend == "inline"
    try:
        if action == "prepare":
            await prepare(settings)
        elif action == "verify":
            await verify(settings)
        else:
            await disable()
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "verify", "disable"])
    asyncio.run(main(parser.parse_args().action))
