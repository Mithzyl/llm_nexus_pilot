"""Cross-user authorization regression audit included in default pytest discovery.

Run from the repository root:
apps/api/.venv/bin/pytest -c apps/api/pyproject.toml \
    apps/api/tests/test_resource_authorization.py -q -s

Uses real password/session resolution, isolated SQLite and memory object storage.
Model calls use existing fake providers only. Writes synthetic data exclusively.
Outputs a status-only JSON report under output/security; never exports cookies or keys.
The final assertion intentionally fails if isolation requirements are not satisfied.
"""

import hashlib
import json
from pathlib import Path

import httpx
import pytest
from test_authentication import login, provision_user
from test_context_builds import create_model_capability
from test_memories import memory_payload
from test_model_attempt_run_artifact_queries import _create_attempt, _create_task

from nexuspilot_api.core.config import get_settings
from nexuspilot_api.main import app


@pytest.mark.parametrize("owner, other", [("audit-a", "audit-b"), ("audit-b", "audit-a")])
async def test_backend_cross_user_access(
    resource_client, test_database_session_factory, monkeypatch, owner, other
):
    """Require real user authorization for legacy and general Agent resources and source text."""
    monkeypatch.setenv("NEXUSPILOT_AUTHENTICATION_ENABLED", "true")
    get_settings.cache_clear()
    client = resource_client
    results = []

    async def create(path, payload=None, **kwargs):
        """Create owner fixtures, failing immediately if the audit setup is not valid."""
        if kwargs.get("headers", {}).get("X-Internal-API-Key"):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
                headers={"X-API-Key": get_settings().api_key},
            ) as service:
                response = await service.post(f"/api/v1/{path}", json=payload, **kwargs)
        else:
            response = await client.post(f"/api/v1/{path}", json=payload, **kwargs)
        assert response.status_code in {200, 201}, (path, response.status_code, response.text)
        return response.json()

    async def probe(actor, method, path, *, expected=(401, 403, 404), **kwargs):
        """Record status without retaining private response bodies or request credentials."""
        response = await client.request(method, f"/api/v1/{path}", **kwargs)
        results.append(
            {
                "actor": actor,
                "method": method,
                "path": path,
                "expected": list(expected),
                "actual": response.status_code,
                "passed": response.status_code in expected,
            }
        )
        return response

    try:
        for user in (owner, other):
            await provision_user(
                test_database_session_factory, user_id=user, email=f"{user}@example.test"
            )
        assert (await login(client, email=f"{owner}@example.test")).status_code == 200
        client.headers.update(
            {"Origin": "http://test", "X-CSRF-Token": client.cookies["nexuspilot_csrf"]}
        )
        assert (await client.get("/api/v1/auth/me")).json()["user_id"] == owner
        owner_cookies = httpx.Cookies(client.cookies)
        owner_csrf = client.cookies["nexuspilot_csrf"]
        owner_login_id = (await client.get("/api/v1/auth/me")).json()["login_session_id"]
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
            headers={"X-API-Key": get_settings().api_key},
        ) as service:
            await create_model_capability(service, context_window=65536)
        session = await create("sessions", {"user_id": owner, "title": "A_PRIVATE_TITLE"})
        sid = session["session_id"]
        turn = await create(
            f"sessions/{sid}/turns", {"user_id": owner, "content_text": "A_PRIVATE_MESSAGE"}
        )
        rid = turn["run"]["run_id"]
        task = await _create_task(client, rid)
        tid = task["task_id"]
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
            headers={
                "X-API-Key": get_settings().api_key,
                "X-Internal-API-Key": "test-internal-key-long-enough",
            },
        ) as service:
            attempt = await _create_attempt(service, rid, task_id=tid)
        aid = attempt["attempt_id"]
        artifact = await create(
            f"runs/{rid}/artifacts",
            data={"artifact_type": "audit"},
            files={"file": ("a-private.txt", b"A_PRIVATE_BYTES", "text/plain")},
        )
        artifact_id = artifact["artifact_id"]
        context_payload = {
            "user_id": owner,
            "session_id": sid,
            "provider": "openai",
            "model": "test-model",
            "token_budget": 2048,
            "reserved_output_tokens": 256,
        }
        context = await create("context-builds/preview", context_payload)
        cid = context["context_build_id"]
        memory = await create(
            "memories",
            memory_payload(
                {
                    "user_id": owner,
                    "session_id": sid,
                    "run_id": rid,
                    "message_id": turn["message"]["message_id"],
                }
            ),
        )
        mid = memory["memory_id"]
        knowledge = await create(
            "knowledge-documents",
            {"user_id": owner, "document_key": "a-private", "title": "A_PRIVATE_KNOWLEDGE"},
        )
        kid = knowledge["knowledge_document_id"]
        internal = {"X-Internal-API-Key": "test-internal-key-long-enough"}
        project = await create(
            "projects", {"owner_user_id": owner, "project_name": "A_PROJECT"}, headers=internal
        )
        pid = project["project_id"]
        rules = await create(
            "internal/evaluation-rule-sets",
            {
                "name": "audit-rules",
                "schema_version": "v1",
                "rules": [
                    {
                        "rule_key": "length",
                        "rule_type": "input_length",
                        "config_json": {"max_characters": 100},
                    }
                ],
            },
            headers=internal,
        )
        evaluation = await create(
            "evaluations",
            {
                "run_id": rid,
                "task_id": tid,
                "evaluation_type": "audit",
                "rule_set_id": rules["rule_set_id"],
                "idempotency_key": "audit-evaluation",
                "input_text": "A evaluation",
            },
        )
        eid = evaluation["evaluation_id"]
        write_run = await create("runs", {"user_id": owner, "user_request": "A pending run"})
        write_rid = write_run["run_id"]

        # A second Run contains a real private-upload record, unlike legacy Artifact uploads.
        content = b"A_PRIVATE_UPLOAD"
        file = await create(
            "files",
            {
                "filename": "a-private-upload.txt",
                "size_bytes": len(content),
                "content_hash": hashlib.sha256(content).hexdigest(),
                "idempotency_key": "private-upload-a",
            },
        )
        fid = file["file_id"]
        assert (
            await client.put(f"/api/v1/files/{fid}/content", content=content)
        ).status_code == 200
        file_turn = await create(
            f"sessions/{sid}/turns",
            {"user_id": owner, "content_text": "Read my file", "file_ids": [fid]},
        )
        file_rid = file_turn["run"]["run_id"]

        # Model-only workflow uses the existing scripted fake; no paid requests are possible.
        from test_agent_workflows import (
            ScriptedAgentProvider,
            registered_agent_test_model,
            scripted_registry,
            workflow_payload,
        )

        from nexuspilot_api.core.dependencies import get_provider_registry

        async with test_database_session_factory() as db:
            db.add(registered_agent_test_model())
            await db.commit()
        app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(
            ScriptedAgentProvider()
        )
        workflow = await create(f"runs/{rid}/agent-workflows", workflow_payload())
        wid = workflow["workflow_execution_id"]

        # New general Agent bodies and source expansion must use the same actual login checks.
        from test_general_agent import FileReadingProvider, general_payload

        app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(
            FileReadingProvider(fid)
        )
        general = await create(f"runs/{file_rid}/agent-workflows", general_payload("never"))
        assert general["status"] == "completed", general.get("error")
        general_id = general["workflow_execution_id"]
        candidate = next(n for n in general["nodes"] if n["output_type"] == "general_candidate")
        source_path = (f"agent-workflows/{general_id}/nodes/"
                       f"{candidate['node_execution_id']}/claim-sources?claim_key=u1")
        owner_sources = await client.get(f"/api/v1/{source_path}")
        assert owner_sources.status_code == 200
        assert "A_PRIVATE_UPLOAD" in owner_sources.text

        # Switch actual browser cookies, without overriding principal dependencies.
        client.cookies.clear()
        client.headers.pop("X-CSRF-Token", None)
        assert (await login(client, email=f"{other}@example.test")).status_code == 200
        client.headers["X-CSRF-Token"] = client.cookies["nexuspilot_csrf"]
        assert (await client.get("/api/v1/auth/me")).json()["user_id"] == other
        bsession = await create("sessions", {"user_id": other, "title": "B_PRIVATE_TITLE"})
        await probe("B-own", "GET", f"sessions/{bsession['session_id']}", expected=(200,))

        reads = [
            f"users/{owner}",
            f"sessions/{sid}",
            f"sessions/{sid}/messages",
            f"sessions/{sid}/messages/latest",
            f"sessions/{sid}/latest-run",
            f"runs/{rid}",
            f"runs/{rid}/tasks",
            f"tasks/{tid}",
            f"attempts/{aid}",
            f"artifacts/{artifact_id}",
            f"artifacts/{artifact_id}/content",
            f"context-builds/{cid}",
            f"context-builds/{cid}/evidence?user_id={owner}",
            f"memories/{mid}",
            f"knowledge-documents/{kid}",
            f"sessions/{sid}/memory",
            f"runs/{rid}/agent-workflow",
            f"agent-workflows/{wid}",
            f"agent-workflows/{wid}/result",
            f"agent-workflows/{wid}/nodes",
            f"agent-workflows/{wid}/events",
            f"files/{fid}",
            f"files/{fid}/content",
            f"artifacts/{fid}/content",
            f"runs/{file_rid}",
            f"agent-workflows/{general_id}",
            f"agent-workflows/{general_id}/result",
            f"agent-workflows/{general_id}/nodes",
            f"agent-workflows/{general_id}/nodes/{candidate['node_execution_id']}",
            f"agent-workflows/{general_id}/events",
            source_path,
        ]
        for path in reads:
            await probe("B-to-A", "GET", path)
        for path in [f"projects/{pid}", f"evaluations/{eid}"]:
            await probe("B-to-A", "GET", path)
        for path, marker in [
            (f"attempts?run_id={rid}", aid),
            (f"artifacts?run_id={rid}", artifact_id),
            ("sessions", sid),
            (f"sessions?user_id={owner}", sid),
            ("runs", rid),
            (f"runs?user_id={owner}", rid),
            ("files", fid),
        ]:
            response = await client.get(f"/api/v1/{path}")
            safe = response.status_code in {401, 403, 404} or (
                response.status_code == 200 and marker not in response.text
            )
            results.append(
                {
                    "actor": "B-list",
                    "method": "GET",
                    "path": path,
                    "expected": "deny or exclude A",
                    "actual": response.status_code,
                    "contains_A": marker in response.text,
                    "passed": safe,
                }
            )

        await probe("B-to-A", "POST", "context-builds/preview", json=context_payload)
        await probe(
            "B-no-internal-key", "PATCH", f"projects/{pid}", json={"project_name": "B_PROJECT"}
        )
        await probe("B-to-A", "PATCH", f"sessions/{sid}", json={"title": "B_CHANGED_A"})
        await probe(
            "B-to-A",
            "POST",
            f"sessions/{sid}/messages",
            json={"role": "user", "content_text": "B_INJECTED_MESSAGE"},
        )
        await probe(
            "B-forged-owner",
            "POST",
            f"sessions/{sid}/turns",
            json={"user_id": owner, "content_text": "B_FORGED_TURN"},
        )
        await probe("B-to-A", "POST", f"runs/{file_rid}/cancel")
        await probe("B-to-A", "PUT", f"files/{fid}/content", content=content)
        await probe("B-to-A", "DELETE", f"files/{fid}")
        await probe("B-to-A", "DELETE", f"auth/sessions/{owner_login_id}")
        await probe("B-no-internal-key", "POST", "internal/model-catalog-versions", json={})

        # Inspect fake model invocation before terminal-state mutation of A's Run.
        from conftest import fake_provider_registry

        app.dependency_overrides[get_provider_registry] = fake_provider_registry
        await probe(
            "B-to-A",
            "POST",
            "responses",
            json={
                "run_id": rid,
                "provider": "openai",
                "model": "test-model",
                "input": "B requests a model call on A run",
            },
        )
        await probe(
            "B-to-A",
            "POST",
            f"runs/{write_rid}/tasks",
            json={"task_type": "audit", "title": "B task", "objective": "B write"},
        )
        await probe("B-to-A", "DELETE", f"memories/{mid}")
        await probe("B-to-A", "PATCH", f"knowledge-documents/{kid}", json={"deactivate": True})
        await probe("B-to-A", "PATCH", f"users/{owner}", json={"display_name": "B_CHANGED_A_USER"})
        await probe("B-to-A", "POST", f"runs/{write_rid}/cancel")

        client.cookies.clear()
        client.headers.pop("X-CSRF-Token", None)
        for path in [
            "auth/me",
            f"sessions/{sid}/messages/latest",
            f"context-builds/{cid}",
            f"files/{fid}",
        ]:
            await probe("service-key-only-no-login", "GET", path)
        for path in [f"sessions/{sid}", f"files/{fid}"]:
            await probe("no-key-no-login", "GET", path, headers={"X-API-Key": ""})

        # Read back owner-visible changes, not just successful HTTP status codes.
        client.cookies.update(owner_cookies)
        client.headers["X-CSRF-Token"] = owner_csrf
        owner_session = await client.get(f"/api/v1/sessions/{sid}")
        owner_messages = await client.get(f"/api/v1/sessions/{sid}/messages/latest")
        evidence = {
            "owner_title_changed": owner_session.json().get("title") == "B_CHANGED_A",
            "foreign_message_persisted": "B_INJECTED_MESSAGE" in owner_messages.text,
            "forged_turn_persisted": "B_FORGED_TURN" in owner_messages.text,
            "private_file_still_readable": (
                await client.get(f"/api/v1/files/{fid}/content")
            ).status_code
            == 200,
        }
        report = {
            "environment": "isolated SQLite, fake model, memory storage, actual A/B login cookies",
            "results": results,
            "owner_readback": evidence,
            "route_inventory": [
                {"path": path, "methods": list(methods)}
                for path, methods in app.openapi()["paths"].items()
            ],
            "scope": "All paths inventoried; live checks are representative, not exhaustive.",
        }
        output = Path(__file__).resolve().parents[3] / "output/security"
        output.mkdir(parents=True, exist_ok=True)
        (output / f"cross-user-access-{owner}.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2)
        )
        failed = [row for row in results if not row["passed"]]
        print(
            json.dumps({"checks": len(results), "failed": len(failed), "owner_readback": evidence})
        )
        assert not failed, json.dumps(failed, ensure_ascii=False)
        assert not evidence["owner_title_changed"]
        assert not evidence["foreign_message_persisted"]
        assert not evidence["forged_turn_persisted"]
        assert evidence["private_file_still_readable"]
    finally:
        get_settings.cache_clear()
