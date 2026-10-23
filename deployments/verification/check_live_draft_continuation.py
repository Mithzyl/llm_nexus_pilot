"""Exercise one injected interruption of real Strata output in an isolated local API.

Run from repo root with PYTHONPATH=apps/api/src:packages/models/src and API Python:
  python deployments/verification/check_live_draft_continuation.py --allow-paid-strata \
    --file-id OWNED_READY_FILE --report NEW.json
Requires auth-disabled local MySQL/MinIO and an enabled Strata registration/key.
Uses an existing authorized attachment. No production hook, fake model output,
automatic retry, or deployment. The injected incomplete call has unknown billing.
"""

import argparse
import asyncio
import json
import subprocess
import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from sqlalchemy import select

from check_live_general_agent import checkpoint
from check_live_strata_long_files import MODEL, inspect, request
from nexuspilot_api.core.config import get_settings
from nexuspilot_api.core.dependencies import get_provider_registry
from nexuspilot_api.infrastructure.database import database_session_factory
from nexuspilot_api.main import app, lifespan
from nexuspilot_api.models import LlmAgentWorkflowEvent
from nexuspilot_models.contracts import ProviderName
from nexuspilot_models.errors import ModelProviderError
from nexuspilot_models.providers.strata import StrataChatProvider
from nexuspilot_models.registry import ProviderRegistry
from nexuspilot_models.transport import HttpTransport


class InterruptedTransport(HttpTransport):
    """Inject inside SSE consumption so normal adapter cleanup closes the real response."""

    def __init__(self, upstream):
        """Share the configured client, not mutable injection state with the normal API."""
        super().__init__(upstream.client, max_retries=upstream.max_retries,
                         backoff_seconds=upstream.backoff_seconds)
        self.eligible = False
        self.injected = False

    @asynccontextmanager
    async def open_sse(self, *args, **kwargs):
        """Interrupt after yielding actual text bytes and before receiving a complete response."""
        async with super().open_sse(*args, **kwargs) as (messages, headers, attempts):
            async def intercepted():
                """Leave tool chunks intact and attach an explicit test-only diagnostic marker."""
                characters, has_tools = 0, False
                async for message in messages:
                    if self.eligible and not self.injected and message.data != "[DONE]":
                        chunk = json.loads(message.data)
                        for choice in chunk.get("choices", []):
                            delta = choice.get("delta") or {}
                            characters += len(delta.get("content") or "")
                            has_tools = has_tools or bool(delta.get("tool_calls"))
                    yield message
                    if self.eligible and not self.injected and characters >= 40 and not has_tools:
                        self.injected = True
                        raise ModelProviderError(
                            "network_error", "Local verification injected stream interruption",
                            raw_error={"verification_injected_interruption": True},
                            transport_attempts=attempts,
                        )
            yield intercepted(), headers, attempts


class InterruptedStrata(StrataChatProvider):
    """Interrupt only one main-agent text stream after a completed attachment read."""

    def __init__(self, upstream):
        """Use the real configured transport without copying credentials into reports."""
        super().__init__(InterruptedTransport(upstream.transport),
                         base_url=upstream.base_url, api_key=upstream.api_key)

    async def stream(self, model_request):
        """Save the actual received prefix; never alter generated text or invoke tools."""
        self.transport.eligible = (
            not self.transport.injected and model_request.metadata.get("agent_role") == "controller"
            and any(m.role.value == "tool" and m.tool_name == "read_file_content"
                    for m in model_request.messages)
        )
        async for event in super().stream(model_request):
            yield event


async def run(args):
    """Create one real workflow with explicit continuation and preserve its result and usage."""
    settings = get_settings()
    if not args.allow_paid_strata or args.report.exists():
        raise RuntimeError("Explicit paid permission and a new report are required")
    if (settings.authentication_enabled
            or urlsplit(settings.database_url).hostname not in {"localhost", "127.0.0.1"}
            or settings.minio_endpoint.split(":")[0] not in {"localhost", "127.0.0.1"}):
        raise RuntimeError("Only auth-disabled local development dependencies are supported")
    report = {"kind": "real_strata_injected_interruption", "status": "preparing",
              "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()}
    checkpoint(args.report, report)
    async with lifespan(app):
        upstream = app.state.provider_registry
        provider = InterruptedStrata(upstream.resolve(ProviderName.STRATA, MODEL))
        registry = ProviderRegistry()
        registry.register(ProviderName.STRATA, provider, allowed_models=frozenset({MODEL}),
            reasoning_capabilities_by_model={MODEL: upstream.reasoning_capabilities(
                ProviderName.STRATA, MODEL,
            )})
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
            base_url="http://127.0.0.1/api/v1/", headers={"X-API-Key": settings.api_key},
            timeout=None) as client:
            owner = (await request(client, "GET", "auth/me"))["user_id"]
            file = await request(client, "GET", f"files/{args.file_id}")
            if file["status"] != "ready":
                raise RuntimeError("Attachment is not ready")
            session = await request(client, "POST", "sessions", json={
                "user_id": owner, "title": "LOCAL Strata interrupted draft verification",
            })
            turn = await request(client, "POST", f"sessions/{session['session_id']}/turns", json={
                "user_id": owner, "file_ids": [args.file_id], "idempotency_key": str(uuid4()),
                "content_text": "读取附件标题页少量原文，用一段约150字中文解释Degree project学分和"
                "论文完整标题，引用原文来源。无需通读、不委派，说明只核对了标题页。",
            })
            run_id = turn["run"]["run_id"]
            report.update(run_id=run_id, session_id=session["session_id"], status="prepared")
            checkpoint(args.report, report)
            print(json.dumps(report), flush=True)
            binding = {"provider": "strata", "model": MODEL, "timeout_seconds": 600}
            previous = app.dependency_overrides.get(get_provider_registry)
            app.dependency_overrides[get_provider_registry] = lambda: registry
            started = time.monotonic()
            try:
                result = await request(client, "POST", f"runs/{run_id}/agent-workflows", json={
                    "workflow_name": "general_agent", "workflow_version": "1.0.0",
                    "execution_profile": "general_tools_v1", "review_policy": "always",
                    "partial_response_policy": "continue_once",
                    "role_bindings": {"controller": binding, "reviewer": binding},
                    "idempotency_key": str(uuid4()), "stream": False,
                })
            finally:
                if previous is None:
                    app.dependency_overrides.pop(get_provider_registry, None)
                else:
                    app.dependency_overrides[get_provider_registry] = previous
            report.update(status=result["status"], result=result, injected=provider.transport.injected,
                          elapsed_seconds=round(time.monotonic() - started, 3))
            report.update(await inspect(run_id))
            async with database_session_factory() as db:
                rows = list(await db.scalars(select(LlmAgentWorkflowEvent).where(
                    LlmAgentWorkflowEvent.run_id == run_id,
                    LlmAgentWorkflowEvent.event_type == "agent.draft.continuation.requested",
                )))
                report["continuations"] = [r.public_payload_json for r in rows]
            checkpoint(args.report, report)
            print(json.dumps({k: report[k] for k in
                ("status", "injected", "elapsed_seconds", "known_usage", "continuations")}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-paid-strata", action="store_true")
    parser.add_argument("--file-id", required=True)
    parser.add_argument("--report", type=Path, required=True)
    asyncio.run(run(parser.parse_args()))
