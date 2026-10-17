"""LOCAL browser fixture: real API/MySQL/MinIO, scripted model, no paid Provider requests.

Run with PYTHONPATH=apps/api/src:packages/models/src and the API virtualenv after
upgrading the isolated verification MySQL on 13306 to head. Reuses the loopback
MinIO on 19000 configured by check_live_worker_evidence. Starts Next on 3305 and
API on 18005 by default; only localhost is bound. Ctrl-C stops both. Synthetic
conversations are retained for SQL/object inspection. No deployment or model calls.
"""

import argparse
import asyncio
import json
from contextlib import asynccontextmanager
import os
from pathlib import Path
import subprocess
import sys

import check_live_worker_evidence as local
import uvicorn
from nexuspilot_models.contracts import (
    ModelResponse,
    ProviderName,
    StreamEvent,
    StreamEventType,
)
from nexuspilot_models.registry import ProviderRegistry
from sqlalchemy import JSON, inspect, select

from nexuspilot_api.core.dependencies import get_provider_registry
from nexuspilot_api.main import app, lifespan
from nexuspilot_api.models import LlmRegisteredModel
from nexuspilot_api.infrastructure.conversation_body_fields import BODY_FIELDS
from nexuspilot_api.services.conversation_body_maintenance import body_model

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps/api/tests"))
from test_agent_workflows import ScriptedAgentProvider  # noqa: E402


class StorageFixtureProvider(ScriptedAgentProvider):
    """Exercise saved context with deterministic quick replies and existing typed Agent fixtures."""

    def _node_output(self, node_key, request):
        """Use the actual Web role binding instead of the unit fixture's researcher-only role."""
        output = super()._node_output(node_key, request)
        if node_key == "controller_planning":
            allowed_roles = json.loads(request.messages[-1].content)["allowed_roles"]
            for task in output["tasks"]:
                task["assigned_role"] = allowed_roles[0]
        return output

    async def generate(self, request):
        """Use no network; return typed workflow outputs or a bounded visible marker."""
        if request.metadata.get("agent_node_key"):
            return await super().generate(request)
        self.requests.append(request)
        text = "BODY_STORAGE_REPLY：本地对象存储验证回复。"
        return ModelResponse(
            text=text,
            finish_reason="stop",
            input_tokens=0,
            output_tokens=0,
            latency_ms=1,
            raw_response={"fixture": "body-storage", "text": text},
        )

    async def stream(self, request):
        """Delay visible output briefly so reload can occur while the actual API is running."""
        yield StreamEvent(type=StreamEventType.STARTED, sequence=1)
        await asyncio.sleep(2)
        response = await self.generate(request)
        yield StreamEvent(
            type=StreamEventType.TEXT_DELTA, sequence=2, data={"delta": response.text}
        )
        yield StreamEvent(
            type=StreamEventType.COMPLETED,
            sequence=3,
            data={"response": response.model_dump(mode="json")},
        )


@asynccontextmanager
async def fixture_lifespan(application):
    """Replace every catalog Provider with a scripted adapter before accepting requests."""
    async with lifespan(application):
        registry = ProviderRegistry()
        async with local.database_session_factory() as db:
            rows = list(await db.scalars(select(LlmRegisteredModel)))
        for provider in ProviderName:
            models = frozenset(
                row.model_identifier for row in rows if row.provider == provider.value
            )
            if models:
                registry.register(
                    provider, StorageFixtureProvider(), allowed_models=models
                )
        application.state.provider_registry = registry
        application.dependency_overrides[get_provider_registry] = lambda: registry
        yield


def main():
    """Launch localhost fixture processes with private server-only environment values."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-port", type=int, default=18005)
    parser.add_argument("--web-port", type=int, default=3305)
    parser.add_argument(
        "--inspect-session", help="Inspect only one synthetic BODY_USER session"
    )
    args = parser.parse_args()
    if args.inspect_session:
        print(
            json.dumps(
                asyncio.run(inspect_session(args.inspect_session)), ensure_ascii=False
            )
        )
        return
    app.router.lifespan_context = fixture_lifespan
    env = {
        **os.environ,
        "NEXUSPILOT_API_URL": f"http://127.0.0.1:{args.api_port}",
        "NEXUSPILOT_API_KEY": local.get_settings().api_key,
    }
    web = subprocess.Popen(
        [
            "npm",
            "run",
            "dev",
            "--",
            "--hostname",
            "127.0.0.1",
            "--port",
            str(args.web_port),
        ],
        cwd=Path(__file__).resolve().parents[2] / "apps/web",
        env=env,
        start_new_session=True,
    )
    try:
        print("LOCAL SCRIPTED PROVIDER: no external model requests", flush=True)
        uvicorn.run(app, host="127.0.0.1", port=args.api_port)
    finally:
        import signal

        os.killpg(web.pid, signal.SIGTERM)
        web.wait(timeout=15)


async def inspect_session(session_id):
    """Read real SQL/object records and verify prior messages reached the second model request."""
    from anyio import to_thread
    from nexuspilot_api.models import (
        LlmMessage,
        LlmRun,
        LlmModelAttempt,
        LlmContextBuild,
    )

    report = {"session_id": session_id, "tables": {}, "external_model_requests": 0}
    try:
        async with local.database_session_factory() as db:
            messages = list(
                await db.scalars(
                    select(LlmMessage)
                    .where(LlmMessage.session_id == session_id)
                    .order_by(LlmMessage.sequence)
                )
            )
            assert len(messages) >= 4
            assert messages[0].content_text.startswith("BODY_USER_ONE")
            assert messages[2].content_text.startswith("BODY_USER_TWO")
            assert all(message.body_ref_json for message in messages)
            runs = list(
                await db.scalars(select(LlmRun).where(LlmRun.session_id == session_id))
            )
            run_ids = [run.run_id for run in runs]
            attempts = list(
                await db.scalars(
                    select(LlmModelAttempt)
                    .where(LlmModelAttempt.run_id.in_(run_ids))
                    .order_by(LlmModelAttempt.started_at)
                )
            )
            assert len(attempts) >= 2
            opened = await db.body_storage.open_object(attempts[1].raw_request_uri)

            def read_request():
                """Read the bounded synthetic request, releasing the MinIO stream on all paths."""
                chunks = iter(opened.chunks)
                try:
                    assert opened.size_bytes < 1024 * 1024
                    return json.loads(b"".join(chunks))
                finally:
                    chunks.close()

            request = await to_thread.run_sync(read_request)
            effective = json.dumps(request["effective_messages"], ensure_ascii=False)
            assert messages[0].content_text in effective
            assert messages[1].content_text in effective
            assert messages[2].content_text in effective
            report["continuation_contains_prior_turn"] = True
            report["message_count"] = len(messages)
            report["run_ids"] = run_ids
            builds = list(
                await db.scalars(
                    select(LlmContextBuild.context_build_id).where(
                        LlmContextBuild.run_id.in_(run_ids)
                    )
                )
            )
            attempt_ids = [item.attempt_id for item in attempts]
            for table, fields in BODY_FIELDS.items():
                model = body_model(table)
                if table == "llm_messages":
                    condition = model.session_id == session_id
                elif table == "llm_context_sources":
                    condition = model.context_build_id.in_(builds)
                elif table in {
                    "llm_tool_calls",
                    "llm_model_response_events",
                    "llm_reasoning_blocks",
                }:
                    condition = model.attempt_id.in_(attempt_ids)
                elif hasattr(model, "run_id"):
                    condition = model.run_id.in_(run_ids)
                else:
                    continue
                # Entity reads validate every object; explicit column projections expose
                # the actual SQL placeholders, not the hydrated application attributes.
                records = list(await db.scalars(select(model).where(condition)))
                columns = inspect(model).columns
                raw = (
                    await db.execute(
                        select(
                            *[columns[field] for field in fields], model.body_ref_json
                        ).where(condition)
                    )
                ).all()
                for row in raw:
                    assert row[-1] is not None
                    for field, value in zip(fields, row[:-1], strict=True):
                        expected = (
                            None
                            if columns[field].nullable
                            else ({} if isinstance(columns[field].type, JSON) else "")
                        )
                        assert value == expected, (table, field)
                report["tables"][table] = {
                    "verified_objects": len(records),
                    "sql_bodies_empty": True if records else None,
                    "status": "passed" if records else "not_exercised",
                }
                if table == "llm_agent_workflow_executions":
                    completed = [r for r in records if r.status.value == "completed"]
                    assert completed, "No completed Agent workflow in this sample"
                    report["completed_workflow_ids"] = [r.workflow_execution_id for r in completed]
        return report
    finally:
        await local.engine.dispose()


if __name__ == "__main__":
    main()
