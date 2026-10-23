"""Verify general Agent ownership with real local MySQL/MinIO and browser-session auth.

Run with API Python and PYTHONPATH=apps/api/src:packages/models/src:
  python deployments/verification/check_local_agent_isolation.py --report NEW.json
Creates two random test users and a private text attachment/workflow. Uses the
explicit no-network seed Provider, not Strata/DeepSeek/OpenAI HTTP. Disables both
test users and revokes their sessions afterward; retains test data for audit.
Authentication is enabled only inside this standalone process, never in .env.
"""

import argparse
import asyncio
import hashlib
import os
from pathlib import Path
import secrets
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from sqlalchemy import update

from check_live_general_agent import checkpoint
from check_live_strata_long_files import request
from refinement_seed import SEED_MODEL, SeedProvider, register_seed
from nexuspilot_api.core.config import get_settings
from nexuspilot_api.core.dependencies import get_provider_registry
from nexuspilot_api.features.identity.models.authentication import UserLoginSession
from nexuspilot_api.features.identity.services.authentication_service import provision_local_password_user
from nexuspilot_api.infrastructure.database import database_session_factory, engine
from nexuspilot_api.main import app, lifespan
from nexuspilot_api.models import User
from nexuspilot_api.models.base import utc_now
from nexuspilot_models.contracts import ProviderName
from nexuspilot_models.registry import ProviderRegistry


async def run(report_path):
    """Check owner/foreign/anonymous access without modifying existing users or infrastructure."""
    settings = get_settings()
    if (report_path.exists() or settings.environment != "development"
            or urlsplit(settings.database_url).hostname not in {"localhost", "127.0.0.1"}
            or settings.minio_endpoint.split(":")[0] not in {"localhost", "127.0.0.1"}):
        raise RuntimeError("New report and local development MySQL/MinIO are required")
    os.environ["NEXUSPILOT_AUTHENTICATION_ENABLED"] = "true"
    get_settings.cache_clear()
    settings = get_settings()
    origin = next(iter(settings.allowed_authentication_origins()))
    users, checks = [], []
    report = {"status": "preparing", "environment": "local MySQL/MinIO, real login",
              "provider_http_requests": 0, "checks": checks, "test_user_ids": users}
    checkpoint(report_path, report)
    try:
        async with lifespan(app), register_seed(database_session_factory):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                base_url="http://127.0.0.1/api/v1/",
                headers={"X-API-Key": settings.api_key, "Origin": origin}, timeout=None) as client:
                accounts = []
                for _ in range(2):
                    user_id = str(uuid4())
                    password = secrets.token_urlsafe(32)
                    email = f"agent-check-{user_id}@example.test"
                    async with database_session_factory() as db:
                        await provision_local_password_user(db, user_id=user_id,
                            display_name="LOCAL Agent isolation verification", email=email,
                            password=password)
                    users.append(user_id)
                    accounts.append((email, password))
                    checkpoint(report_path, report)

                async def login(index):
                    """Resolve a real login cookie and CSRF token without exporting either."""
                    client.cookies.clear()
                    client.headers.pop("X-CSRF-Token", None)
                    await request(client, "POST", "auth/login", json={
                        "email": accounts[index][0], "password": accounts[index][1],
                    })
                    client.headers["X-CSRF-Token"] = client.cookies["nexuspilot_csrf"]
                    me = await request(client, "GET", "auth/me")
                    assert me["user_id"] == users[index]

                await login(0)
                content = b"Degree project 30 credits\nPRIVATE_AGENT_SOURCE\n"
                file = await request(client, "POST", "files", json={
                    "filename": "private-agent-isolation.txt", "size_bytes": len(content),
                    "content_hash": hashlib.sha256(content).hexdigest(),
                    "idempotency_key": str(uuid4()),
                })
                await request(client, "PUT", f"files/{file['file_id']}/content", content=content)
                session = await request(client, "POST", "sessions", json={
                    "user_id": users[0], "title": "LOCAL Agent isolation verification",
                })
                turn = await request(client, "POST", f"sessions/{session['session_id']}/turns",
                    json={"user_id": users[0], "content_text": "Read the attachment",
                          "file_ids": [file["file_id"]], "idempotency_key": str(uuid4())})
                registry = ProviderRegistry()
                registry.register(ProviderName.OPENAI, SeedProvider(file["file_id"]),
                                  allowed_models=frozenset({SEED_MODEL}))
                app.dependency_overrides[get_provider_registry] = lambda: registry
                result = await request(client, "POST", f"runs/{turn['run']['run_id']}/agent-workflows",
                    json={"workflow_name": "general_agent", "workflow_version": "1.0.0",
                          "execution_profile": "general_tools_v1", "review_policy": "never",
                          "role_bindings": {"controller": {
                              "provider": "openai", "model": SEED_MODEL}},
                          "idempotency_key": str(uuid4())})
                assert result["status"] == "completed", result.get("error")
                wid = result["workflow_execution_id"]
                candidate = next(n for n in result["nodes"] if n["output_type"] == "general_candidate")
                source = (f"agent-workflows/{wid}/nodes/{candidate['node_execution_id']}"
                          "/claim-sources?claim_key=u1")
                paths = [f"agent-workflows/{wid}/result", f"agent-workflows/{wid}/nodes",
                         f"agent-workflows/{wid}/events", source,
                         f"messages/{result['final_output']['assistant_message_id']}",
                         f"files/{file['file_id']}/content"]
                report.update(run_id=turn["run"]["run_id"], workflow_id=wid,
                              session_id=session["session_id"])
                for actor in (0, 1, None):
                    if actor is None:
                        client.cookies.clear()
                        client.headers.pop("X-CSRF-Token", None)
                    elif actor == 1:
                        await login(1)
                    for path in paths:
                        response = await client.get(path)
                        passed = response.status_code == 200 if actor == 0 else (
                            response.status_code in {401, 403, 404}
                            and "PRIVATE_AGENT_SOURCE" not in response.text)
                        if actor == 0 and path == source:
                            passed = passed and "PRIVATE_AGENT_SOURCE" in response.text
                        checks.append({"actor": "owner" if actor == 0 else
                            "foreign" if actor == 1 else "anonymous", "path": path,
                            "status": response.status_code, "passed": passed})
                assert all(c["passed"] for c in checks), "Ownership check failed"
                report["status"] = "passed"
    finally:
        app.dependency_overrides.pop(get_provider_registry, None)
        if users:
            async with database_session_factory() as db:
                await db.execute(update(User).where(User.user_id.in_(users)).values(is_active=False))
                await db.execute(update(UserLoginSession).where(
                    UserLoginSession.user_id.in_(users), UserLoginSession.revoked_at.is_(None),
                ).values(revoked_at=utc_now(), revoked_reason="local_verification_finished"))
                await db.commit()
            report["test_users_disabled_and_sessions_revoked"] = True
        checkpoint(report_path, report)
        await engine.dispose()
    print({"status": report["status"], "checks": len(checks), "provider_http_requests": 0})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    asyncio.run(run(parser.parse_args().report))
