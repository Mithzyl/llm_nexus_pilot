"""Real local MySQL/MinIO and browser attachment validation with a fixed, non-network Provider.

From apps/api: PYTHONPATH=src:tests .venv/bin/python tests/manual_project_attachment_validation.py
serve|prepare|verify|disable. Set NEXUSPILOT_AUTHENTICATION_ENABLED=true,
NEXUSPILOT_WORKFLOW_EXECUTION_BACKEND=inline, and for serve set
NEXUSPILOT_AUTHENTICATION_ALLOWED_ORIGINS=http://127.0.0.1:3100.
Serve binds 127.0.0.1:8002; browser BFF uses port3100.
Private generated accounts/cookies stay in ignored deployments/data. No existing records are
overwritten, no objects are deleted, and disable only revokes generated users and fixture model.
"""

import argparse
import asyncio
import hashlib
import json
from contextlib import asynccontextmanager

import httpx
import manual_project_workspace_validation as base
import uvicorn
from nexuspilot_models.contracts import ModelResponse, ProviderName, ToolCall
from nexuspilot_models.registry import ProviderRegistry
from sqlalchemy import select, text, update
from sqlalchemy.engine import make_url
from test_agent_workflows import registered_agent_test_model
from test_response_file_tools import QuickFileProvider

from nexuspilot_api.core.config import get_settings
from nexuspilot_api.infrastructure.database import database_session_factory, engine
from nexuspilot_api.infrastructure.object_storage import get_object_storage
from nexuspilot_api.main import app, create_price_catalog
from nexuspilot_api.models import LlmMessage, LlmRegisteredModel, UserFile
from nexuspilot_api.models.enums import ModelRegistrationStatus
from nexuspilot_api.services.user_file_service import read_parsed_file

base.PRIVATE = base.ROOT / "deployments/data/project-attachment-validation-20261026"
base.MANIFEST = base.PRIVATE / "manifest.json"
base.REPORT = base.ROOT / "output/security/project-attachment-real-20261026.json"
base.UPLOAD = b"UNIQUE_FILE_TEXT_2718\nSynthetic selected project file.\n"
MODEL = "project-attachment-fixture-20261026"


class FollowupFileProvider(QuickFileProvider):
    """Read the real attachment on each turn while permitting the saved prior answer in history."""

    async def generate(self, request):
        """Request list/read tools and reject automatic source-text injection before reading."""
        results = [message for message in request.messages if message.role.value == "tool"]
        if not results:
            assert all(
                "Synthetic selected project file." not in (m.content or "")
                for m in request.messages
            )
            calls = [ToolCall(id="list-1", name="list_run_files", arguments={})]
        elif len(results) == 1:
            files = json.loads(results[0].content)["result"]["files"]
            assert len(files) == 1
            calls = [
                ToolCall(
                    id="read-2",
                    name="read_file_content",
                    arguments={"file_id": files[0]["file_id"]},
                )
            ]
        else:
            assert "UNIQUE_FILE_TEXT_2718" in results[-1].content
            return ModelResponse(
                text="File answer: UNIQUE_FILE_TEXT_2718",
                finish_reason="stop",
                input_tokens=30,
                output_tokens=10,
                latency_ms=1,
                raw_response={"fixture": True},
            )
        return ModelResponse(
            tool_calls=calls,
            finish_reason="tool_calls",
            input_tokens=30,
            output_tokens=10,
            latency_ms=1,
            raw_response={"fixture": True},
        )


def local_settings():
    """Refuse remote infrastructure and worker processes that could use real model adapters."""
    settings = get_settings()
    assert make_url(settings.database_url).host in {"127.0.0.1", "localhost"}
    assert settings.minio_endpoint.split(":")[0] in {"127.0.0.1", "localhost"}
    assert settings.authentication_enabled and settings.workflow_execution_backend == "inline"
    return settings


@asynccontextmanager
async def fixture_lifespan(application):
    """Register only a fixed in-process provider, never supplier HTTP adapters."""
    settings = local_settings()
    registry = ProviderRegistry()
    registry.register(
        ProviderName.OPENAI, FollowupFileProvider(), allowed_models=frozenset({MODEL})
    )
    application.state.provider_registry = registry
    application.state.price_catalog = create_price_catalog(settings)
    try:
        yield
    finally:
        await engine.dispose()


async def prepare():
    """Provision synthetic users and an immutable saved file for browser selection."""
    settings = local_settings()
    if not base.MANIFEST.exists():
        await base.prepare(settings)
    manifest = json.loads(base.MANIFEST.read_text())
    if "project_id" in manifest:
        raise RuntimeError("Preparation already complete; reuse the saved project and session")
    if "file_id" not in manifest:
        async with httpx.AsyncClient(
            base_url=base.API,
            headers={"X-API-Key": settings.api_key, "Origin": base.ORIGIN},
            timeout=30,
            trust_env=False,
        ) as client:
            await base.login(client, manifest["accounts"][0])
            source = base.checked(
                await client.post(
                    "/api/v1/files",
                    json={
                        "filename": "selected.txt",
                        "size_bytes": len(base.UPLOAD),
                        "content_hash": hashlib.sha256(base.UPLOAD).hexdigest(),
                        "idempotency_key": "attachment-validation-upload",
                    },
                ),
                201,
            )
            base.checked(
                await client.put(f"/api/v1/files/{source['file_id']}/content", content=base.UPLOAD)
            )
            manifest["file_id"] = source["file_id"]
            base.private_json(base.PRIVATE / "browser-a.json", base.browser_state(client))
            client.cookies.clear()
            await base.login(client, manifest["accounts"][1])
            base.private_json(base.PRIVATE / "browser-b.json", base.browser_state(client))
            base.private_json(base.MANIFEST, manifest)
    async with database_session_factory() as db:
        model = await db.scalar(
            select(LlmRegisteredModel).where(LlmRegisteredModel.model_identifier == MODEL)
        )
        if model is None:
            model = registered_agent_test_model(MODEL)
            model.display_name = "本地固定响应验证（不调用供应商）"
            db.add(model)
            await db.commit()
    async with httpx.AsyncClient(
        base_url=base.API,
        headers={"X-API-Key": settings.api_key, "Origin": base.ORIGIN},
        timeout=30,
        trust_env=False,
    ) as client:
        base.checked(
            await client.post(
                "/api/v1/internal/model-catalog-versions",
                headers={"X-Internal-API-Key": settings.internal_api_key.get_secret_value()},
                json={
                    "provider": "openai",
                    "model": MODEL,
                    "catalog_version": "local-20261026",
                    "source": "synthetic-validation",
                    "context_window": 65536,
                    "supports_tools": True,
                    "supports_streaming": True,
                    "tokenizer_name": "utf8_bytes_upper_bound",
                    "tokenizer_version": "v1",
                },
            )
        )
        await base.login(client, manifest["accounts"][0])
        project = base.checked(
            await client.post("/api/v1/projects", json={"project_name": "项目附件真实存储验证"}),
            201,
        )
        pid = project["project_id"]
        saved = base.checked(
            await client.post(
                f"/api/v1/projects/{pid}/files",
                json={
                    "file_id": manifest["file_id"],
                    "filename": "selected.txt",
                    "idempotency_key": "save-selected-20261026",
                },
            ),
            201,
        )
        session = base.checked(
            await client.post(
                f"/api/v1/projects/{pid}/sessions",
                json={"title": "项目附件真实验证", "idempotency_key": "session-20261026"},
            ),
            201,
        )
        manifest.update(
            project_id=pid,
            project_file_id=saved["project_file_id"],
            session_id=session["session_id"],
        )
        base.private_json(base.MANIFEST, manifest)
    print(
        json.dumps({key: manifest[key] for key in ("project_id", "project_file_id", "session_id")})
    )


async def verify():
    """Check browser-persisted turns, actual MinIO parses and foreign-user API rejection."""
    settings = local_settings()
    manifest = json.loads(base.MANIFEST.read_text())
    sid, pid, fid = (manifest[key] for key in ("session_id", "project_id", "project_file_id"))
    report = {
        "database": "real local MySQL",
        "storage": "real local MinIO",
        "provider": "fixed in-process; no paid requests",
        "session_id": sid,
        "checks": {},
    }
    async with database_session_factory() as db:
        report["migration"] = await db.scalar(text("SELECT version_num FROM alembic_version"))
        messages = list(
            await db.scalars(
                select(LlmMessage).where(LlmMessage.session_id == sid).order_by(LlmMessage.sequence)
            )
        )
        assert len(messages) == 4, len(messages)
        # ORM intentionally hydrates bodies; inspect SQL columns directly for storage assertions.
        raw = list(
            (
                await db.execute(
                    text(
                        "SELECT content_text, body_ref_json FROM llm_messages WHERE session_id=:sid"
                    ),
                    {"sid": sid},
                )
            ).mappings()
        )
        assert all(not row["content_text"] and row["body_ref_json"] for row in raw)
        files = messages[0].metadata_json["files"]
        assert len(files) == 1 and files[0]["project_attachment"]["project_file_id"] == fid
        assert not messages[2].metadata_json.get("files")
        attachment = await db.get(UserFile, files[0]["file_id"])
        parsed = await read_parsed_file(get_object_storage(), attachment)
        assert "UNIQUE_FILE_TEXT_2718" in parsed.model_dump_json()
        report["run_ids"] = [messages[0].run_id, messages[2].run_id]
        report["tool_calls"] = []
        for run_id in report["run_ids"]:
            calls = list(
                (
                    await db.execute(
                        text(
                            "SELECT c.tool_name,c.status FROM llm_tool_calls c JOIN llm_attempts a "
                            "ON a.attempt_id=c.attempt_id WHERE a.run_id=:run ORDER BY c.started_at"
                        ),
                        {"run": run_id},
                    )
                ).mappings()
            )
            # MySQL timestamps here have second precision; verify the two calls, not tied row order.
            assert sorted(call["tool_name"] for call in calls) == [
                "list_run_files",
                "read_file_content",
            ]
            assert all(call["status"] == "completed" for call in calls)
            report["tool_calls"].append({"run_id": run_id, "calls": [dict(call) for call in calls]})
        report["checks"].update(
            two_persisted_turns=True,
            message_bodies_in_minio=True,
            selected_source_preserved=True,
            followup_without_reattachment=True,
            parsed_object_read_from_minio=True,
        )
    async with httpx.AsyncClient(
        base_url=base.API,
        headers={"X-API-Key": settings.api_key, "Origin": base.ORIGIN},
        timeout=30,
        trust_env=False,
    ) as client:
        await base.login(client, manifest["accounts"][0])
        response = base.checked(
            await client.get(f"/api/v1/sessions/{sid}/messages/latest?limit=100")
        )
        assert sum("File answer: UNIQUE_FILE_TEXT_2718" in str(m) for m in response["items"]) == 2
        client.cookies.clear()
        await base.login(client, manifest["accounts"][1])
        checks = [
            await client.get(f"/api/v1/projects/{pid}/files"),
            await client.get(f"/api/v1/projects/{pid}/files/{fid}/content"),
            await client.post(
                f"/api/v1/projects/{pid}/files/{fid}/attachments",
                json={"session_id": sid, "idempotency_key": "foreign-attempt"},
            ),
            await client.get(f"/api/v1/sessions/{sid}/messages/latest"),
            await client.get(f"/api/v1/files/{attachment.file_id}/content"),
        ]
        assert all(response.status_code == 404 for response in checks)
        report["checks"]["cross_user_api_denied"] = True
    base.REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False))


async def main(action):
    """Run explicit local validation actions and release SQL connections."""
    local_settings()
    try:
        if action == "prepare":
            await prepare()
        elif action == "verify":
            await verify()
        else:
            await base.disable()
            async with database_session_factory() as db:
                await db.execute(
                    update(LlmRegisteredModel)
                    .where(LlmRegisteredModel.model_identifier == MODEL)
                    .values(status=ModelRegistrationStatus.DISABLED)
                )
                await db.commit()
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["serve", "prepare", "verify", "disable"])
    action = parser.parse_args().action
    if action == "serve":
        app.router.lifespan_context = fixture_lifespan
        uvicorn.run(app, host="127.0.0.1", port=8002, access_log=False)
    else:
        asyncio.run(main(action))
