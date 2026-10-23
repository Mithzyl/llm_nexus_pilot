"""Verify personal keys against the running localhost API and its real MySQL/MinIO.

Run with API Python and PYTHONPATH=apps/api/src:packages/models/src:
  python deployments/verification/check_local_personal_keys.py --report NEW.json
Requires migrations through 0026 and a login-enabled API on 127.0.0.1:8000 using
the same local .env database. Creates two random real password accounts, logs in
over HTTP, then disables only those accounts and revokes their keys/sessions.
Retains synthetic records for inspection. Never sends a model generation request.
Secrets exist only in memory; the report contains checks and synthetic IDs.
"""

import argparse
import asyncio
import hashlib
import json
import secrets
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from sqlalchemy import text, update

import nexuspilot_api.models  # noqa: F401
from nexuspilot_api.core.config import get_settings
from nexuspilot_api.features.identity.models.authentication import UserLoginSession
from nexuspilot_api.features.identity.models.personal_api_key import UserApiKey
from nexuspilot_api.features.identity.services.authentication_service import (
    provision_local_password_user,
)
from nexuspilot_api.infrastructure.database import database_session_factory, engine
from nexuspilot_api.models import User
from nexuspilot_api.models.base import utc_now


async def verify(report_path):
    """Exercise real HTTP authentication and owner checks, with scoped cleanup on failure."""
    settings = get_settings()
    if (
        report_path.exists()
        or settings.environment != "development"
        or urlsplit(settings.database_url).hostname not in {"localhost", "127.0.0.1"}
        or settings.minio_endpoint.split(":")[0] not in {"localhost", "127.0.0.1"}
    ):
        raise RuntimeError("New report and local development database/storage required")
    users, accounts, checks = [], [], []
    report = {
        "status": "running",
        "checks": checks,
        "test_user_ids": users,
        "provider_generation_requests": 0,
        "transport": "real localhost HTTP",
    }
    origin = sorted(settings.allowed_authentication_origins())[0]
    try:
        async with database_session_factory() as db:
            report["migration"] = (
                await db.execute(text("SELECT version_num FROM alembic_version"))
            ).scalar_one()
            assert report["migration"] == "20261023_0026"
        async with httpx.AsyncClient(
            base_url="http://127.0.0.1:8000/api/v1/", timeout=30
        ) as client:

            async def call(label, method, path, expected, **kwargs):
                """Record status only, never HTTP bodies that may contain credentials."""
                response = await client.request(method, path, **kwargs)
                checks.append(
                    {
                        "check": label,
                        "status": response.status_code,
                        "passed": response.status_code == expected,
                    }
                )
                if response.status_code != expected:
                    raise RuntimeError(
                        f"{label}: expected {expected}, got {response.status_code}"
                    )
                return response

            for index in range(2):
                user_id = str(uuid4())
                email, password = (
                    f"key-check-{user_id}@example.test",
                    secrets.token_urlsafe(32),
                )
                async with database_session_factory() as db:
                    await provision_local_password_user(
                        db,
                        user_id=user_id,
                        display_name="LOCAL personal key verification",
                        email=email,
                        password=password,
                    )
                users.append(user_id)
                client.cookies.clear()
                await call(
                    f"login_{index}",
                    "POST",
                    "auth/login",
                    200,
                    headers={"Origin": origin},
                    json={"email": email, "password": password},
                )
                csrf = {
                    "Origin": origin,
                    "X-CSRF-Token": client.cookies["nexuspilot_csrf"],
                }
                created = (
                    await call(
                        f"create_key_{index}",
                        "POST",
                        "me/api-keys",
                        201,
                        headers=csrf,
                        json={
                            "name": "local-verification",
                            "scopes": ["read", "execute"],
                        },
                    )
                ).json()
                listing = await call(f"list_key_{index}", "GET", "me/api-keys", 200)
                assert (
                    created["key"] not in listing.text
                    and "verifier" not in listing.text
                )
                accounts.append(
                    {"cookies": dict(client.cookies), "csrf": csrf, "key": created}
                )
                client.cookies.clear()
                bearer = {"Authorization": "Bearer " + created["key"]}
                me = (
                    await call(
                        f"bearer_me_{index}", "GET", "auth/me", 200, headers=bearer
                    )
                ).json()
                assert me["user_id"] == user_id
                session = (
                    await call(
                        f"session_create_{index}",
                        "POST",
                        "sessions",
                        201,
                        headers=bearer,
                        json={"user_id": user_id, "title": "LOCAL personal key check"},
                    )
                ).json()
                run = (
                    await call(
                        f"run_create_{index}",
                        "POST",
                        "runs",
                        201,
                        headers=bearer,
                        json={"user_id": user_id, "user_request": "No model execution"},
                    )
                ).json()
                body = b"Synthetic private attachment; no personal content."
                file = (
                    await call(
                        f"file_reserve_{index}",
                        "POST",
                        "files",
                        201,
                        headers=bearer,
                        json={
                            "filename": "key-check.txt",
                            "size_bytes": len(body),
                            "content_hash": hashlib.sha256(body).hexdigest(),
                            "idempotency_key": str(uuid4()),
                        },
                    )
                ).json()
                await call(
                    f"file_upload_{index}",
                    "PUT",
                    f"files/{file['file_id']}/content",
                    200,
                    headers=bearer,
                    content=body,
                )
                accounts[-1]["paths"] = [
                    f"sessions/{session['session_id']}",
                    f"runs/{run['run_id']}",
                    f"files/{file['file_id']}/content",
                ]
                accounts[-1]["run_id"] = run["run_id"]

            for index, account in enumerate(accounts):
                bearer = {"Authorization": "Bearer " + account["key"]["key"]}
                for number, path in enumerate(account["paths"]):
                    await call(
                        f"own_resource_{index}_{number}",
                        "GET",
                        path,
                        200,
                        headers=bearer,
                    )
                for number, path in enumerate(accounts[1 - index]["paths"]):
                    await call(
                        f"foreign_resource_{index}_{number}",
                        "GET",
                        path,
                        404,
                        headers=bearer,
                    )
                await call(
                    f"foreign_cancel_{index}",
                    "POST",
                    f"runs/{accounts[1 - index]['run_id']}/cancel",
                    404,
                    headers=bearer,
                )
                await call(
                    f"own_cancel_{index}",
                    "POST",
                    f"runs/{account['run_id']}/cancel",
                    200,
                    headers=bearer,
                )
                await call(
                    f"key_cannot_manage_{index}",
                    "GET",
                    "me/api-keys",
                    401,
                    headers=bearer,
                )
                client.cookies.update(account["cookies"])
                await call(
                    f"identity_conflict_{index}", "GET", "auth/me", 400, headers=bearer
                )
                await call(
                    f"revoke_{index}",
                    "DELETE",
                    f"me/api-keys/{account['key']['api_key_id']}",
                    204,
                    headers=account["csrf"],
                )
                client.cookies.clear()
                await call(
                    f"revoked_read_{index}",
                    "GET",
                    account["paths"][0],
                    401,
                    headers=bearer,
                )
                await call(
                    f"revoked_write_{index}",
                    "POST",
                    "runs",
                    401,
                    headers=bearer,
                    json={"user_id": users[index], "user_request": "Must not create"},
                )
        report["status"] = "passed"
    finally:
        try:
            async with database_session_factory() as db:
                if users:
                    await db.execute(
                        update(User)
                        .where(User.user_id.in_(users))
                        .values(is_active=False)
                    )
                    await db.execute(
                        update(UserApiKey)
                        .where(UserApiKey.user_id.in_(users))
                        .values(revoked_at=utc_now())
                    )
                    await db.execute(
                        update(UserLoginSession)
                        .where(UserLoginSession.user_id.in_(users))
                        .values(
                            revoked_at=utc_now(),
                            revoked_reason="local_verification_finished",
                        )
                    )
                    await db.commit()
                report["cleanup"] = (
                    "test users disabled; personal keys and sessions revoked"
                )
        finally:
            if report["status"] != "passed":
                report["status"] = "failed"
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(
                json.dumps(report, ensure_ascii=False, indent=2) + "\n"
            )
            await engine.dispose()
    print(
        json.dumps(
            {
                "status": report["status"],
                "checks": len(checks),
                "report": str(report_path),
            }
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    asyncio.run(verify(parser.parse_args().report))
