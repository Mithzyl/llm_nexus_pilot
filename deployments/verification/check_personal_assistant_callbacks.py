"""Check current PA JSON callbacks with real local services, without model generation.

Run with Nexus API Python and PYTHONPATH=apps/api/src:packages/models/src after
check_personal_assistant_app.py prepare/configure/serve and Nexus startup on8001.
Uses synthetic accounts only. Writes status/count/ID metadata, never result bodies.
"""

import asyncio
import json
from pathlib import Path
from uuid import uuid4

import httpx

import nexuspilot_api.models  # noqa: F401
from nexuspilot_api.features.identity.models.applications import (
    ApplicationUserMapping,
    RunApplicationContext,
)
from nexuspilot_api.infrastructure.database import database_session_factory, engine
from nexuspilot_api.models import LlmRun
from nexuspilot_api.services.application_tools import (
    STATIC_APP_TOOLS,
    application_tool_configurations,
)
from nexuspilot_api.services.remote_tool_adapter import RemoteToolAdapter


async def verify():
    """Validate persisted App identity, all three callback schemas and replay isolation."""
    state = json.loads(
        Path("/private/tmp/nexus-personal-assistant-20261023/state.json").read_text()
    )
    config = application_tool_configurations()["personal_assistant"]
    adapter = RemoteToolAdapter(
        base_url=config.base_url,
        token=config.service_token.get_secret_value(),
        allow_local_http=True,
    )
    headers = {
        "Authorization": "Bearer " + config.inbound_key.get_secret_value(),
        "X-Application-Id": "personal_assistant",
        "X-External-User-Id": state["accounts"][0]["user_id"],
    }
    report = {
        "status": "running",
        "kind": "real local callback HTTP; no model",
        "checks": [],
    }
    path = Path("output/security/personal-assistant-callbacks-20261023.json")
    if path.exists():
        raise RuntimeError("Do not overwrite previous evidence")
    try:
        async with httpx.AsyncClient(trust_env=False, timeout=20) as client:
            created = await client.post(
                "http://127.0.0.1:8001/api/v1/app/runs",
                headers=headers,
                json={"user_request": "Synthetic callback verification only"},
            )
            assert created.status_code == 201
            run_id = created.json()["run_id"]
            report["run_id"] = run_id
            async with database_session_factory() as db:
                context = await db.get(RunApplicationContext, run_id)
                mapping = await db.get(ApplicationUserMapping, context.mapping_id)
                run = await db.get(LlmRun, run_id)
                assert mapping.application_id == "personal_assistant"
                assert mapping.external_user_id == headers["X-External-User-Id"]
                assert run.user_id == mapping.pilot_principal_id
                subject = mapping.external_user_id
                report["persisted_owner_correct"] = True
            calls = [
                ("personal_assistant_list_tasks", {}),
                ("personal_assistant_search_notes", {"query": "Synthetic"}),
                (
                    "personal_assistant_list_calendar_events",
                    {"start_date": "2026-10-23", "end_date": "2026-10-24"},
                ),
            ]
            for name, args in calls:
                tool = next(
                    t for t in STATIC_APP_TOOLS["personal_assistant"] if t.name == name
                )
                call_id = str(uuid4())
                result = await adapter.execute(
                    path=config.operations[name],
                    execution_id=call_id,
                    application_id="personal_assistant",
                    external_user_id=subject,
                    tool_name=tool.canonical_name,
                    version=tool.version,
                    arguments=tool.arguments.model_validate(args),
                    output=tool.output,
                    timeout_seconds=10,
                    max_result_bytes=65536,
                )
                envelope = {
                    "tool_execution_id": call_id,
                    "application_id": "personal_assistant",
                    "external_user_id": subject,
                    "tool_name": tool.canonical_name,
                    "tool_version": tool.version,
                    "arguments": tool.arguments.model_validate(args).model_dump(
                        mode="json"
                    ),
                }
                callback_headers = {
                    "Authorization": "Bearer " + config.service_token.get_secret_value()
                }
                replay = await client.post(
                    config.base_url + config.operations[name],
                    headers=callback_headers,
                    json=envelope,
                )
                assert (
                    replay.status_code == 200
                    and replay.headers["x-idempotent-replay"] == "true"
                )
                assert replay.json() == result
                foreign = await client.post(
                    config.base_url + config.operations[name],
                    headers=callback_headers,
                    json={
                        **envelope,
                        "external_user_id": state["accounts"][1]["user_id"],
                    },
                )
                assert foreign.status_code == 403
                invalid = await client.post(
                    config.base_url + config.operations[name],
                    headers=callback_headers,
                    json={
                        **envelope,
                        "tool_execution_id": str(uuid4()),
                        "arguments": {**args, "user_id": "foreign"},
                    },
                )
                assert invalid.status_code == 422
                report["checks"].append(
                    {
                        "tool": name,
                        "call_id": call_id,
                        "items": len(result["items"]),
                        "schema_validated": True,
                        "replay": True,
                        "cross_user_replay_rejected": True,
                        "identity_argument_rejected": True,
                    }
                )
            foreign_headers = {
                **headers,
                "X-External-User-Id": state["accounts"][1]["user_id"],
            }
            cancel_url = f"http://127.0.0.1:8001/api/v1/app/runs/{run_id}/cancel"
            assert (
                await client.post(cancel_url, headers=foreign_headers)
            ).status_code == 404
            cancelled = await client.post(cancel_url, headers=headers)
            assert (
                cancelled.status_code == 200
                and cancelled.json()["status"] == "cancelled"
            )
            report["own_cancel"] = True
            report["foreign_cancel_rejected"] = True
            report["status"] = "passed"
    finally:
        if report["status"] != "passed":
            report["status"] = "failed"
        path.write_text(json.dumps(report, indent=2) + "\n")
        await engine.dispose()
    print(json.dumps(report))


if __name__ == "__main__":
    asyncio.run(verify())
