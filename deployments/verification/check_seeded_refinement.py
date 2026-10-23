"""Run one explicitly paid, locally seeded Strata refinement acceptance.

From repo root, with PYTHONPATH=apps/api/src:packages/models/src and the API venv:
  python deployments/verification/check_seeded_refinement.py --allow-paid-strata --report NEW.json
Requires development auth-disabled local MySQL/MinIO, the configured file executor,
and the existing enabled Strata model/endpoint/key. Uses an in-process API, not a
new public endpoint; the ordinary browser/API can read the saved local session.
Never resubmits an existing report. The first two attempts are explicit local
fixture outputs; only subsequent Strata attempts are paid/provider evidence.
"""

import argparse
import asyncio
import hashlib
import json
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from check_live_general_agent import (
    checkpoint,
    execution_checkpoint_evidence,
    storage_evidence,
)
from check_live_strata_long_files import MODEL, inspect, request
from nexuspilot_api.core.config import get_settings
from nexuspilot_api.core.dependencies import get_provider_registry
from nexuspilot_api.features.agent_runtime.schemas.agent_workflows import (
    AgentModelBinding,
)
from nexuspilot_api.features.agent_runtime.services.general_agent_orchestrator import (
    GeneralAgentOrchestrator,
)
from nexuspilot_api.infrastructure.database import database_session_factory
from nexuspilot_api.main import app, lifespan
from nexuspilot_api.models import LlmModelAttempt
from nexuspilot_models.contracts import ProviderName
from nexuspilot_models.registry import ProviderRegistry
from refinement_seed import SEED_MODEL, SeedProvider, register_seed, seed_first_turn
from sqlalchemy import select


async def run(args):
    """Create one scoped test Run, execute real post-seed turns, and retain honest accounting."""
    settings = get_settings()
    if not args.allow_paid_strata or args.report.exists():
        raise RuntimeError(
            "Explicit paid permission and a new report path are required"
        )
    if (
        urlsplit(settings.database_url).hostname not in {"localhost", "127.0.0.1"}
        or settings.minio_endpoint.split(":")[0] not in {"localhost", "127.0.0.1"}
        or settings.authentication_enabled
    ):
        raise RuntimeError(
            "Only the auth-disabled local development fixture is supported"
        )
    report = {
        "kind": "seeded_refinement",
        "provider": "strata",
        "seed_model": SEED_MODEL,
        "commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True
        ).strip(),
        "status": "preparing",
        "started_at": datetime.now(UTC).isoformat(),
    }
    checkpoint(args.report, report)
    async with lifespan(app):
        upstream = app.state.provider_registry
        real_provider = upstream.resolve(ProviderName.STRATA, MODEL)
        registry = ProviderRegistry()
        registry.register(
            ProviderName.STRATA,
            real_provider,
            allowed_models=frozenset({MODEL}),
            reasoning_capabilities_by_model={
                MODEL: upstream.reasoning_capabilities(
                    ProviderName.STRATA,
                    MODEL,
                )
            },
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://127.0.0.1/api/v1/",
            headers={"X-API-Key": settings.api_key},
            timeout=None,
        ) as client:
            owner = (await request(client, "GET", "auth/me"))["user_id"]
            content = args.file.read_bytes()
            file = await request(
                client,
                "POST",
                "files",
                json={
                    "filename": args.file.name,
                    "size_bytes": len(content),
                    "content_hash": hashlib.sha256(content).hexdigest(),
                    "idempotency_key": str(uuid4()),
                },
            )
            uploaded = await request(
                client, "PUT", f"files/{file['file_id']}/content", content=content
            )
            if uploaded["status"] != "ready":
                raise RuntimeError("Verification attachment was not parsed")
            session = await request(
                client,
                "POST",
                "sessions",
                json={
                    "user_id": owner,
                    "title": "LOCAL seeded refinement verification",
                },
            )
            turn = await request(
                client,
                "POST",
                f"sessions/{session['session_id']}/turns",
                json={
                    "user_id": owner,
                    "content_text": "请依据附件标题页原文，用一句话回答Degree project的学分并引用来源。",
                    "file_ids": [file["file_id"]],
                    "idempotency_key": str(uuid4()),
                },
            )
            run_id = turn["run"]["run_id"]
            report.update(
                run_id=run_id,
                session_id=session["session_id"],
                file_id=file["file_id"],
                status="prepared",
                source_sha256=hashlib.sha256(content).hexdigest(),
            )
            checkpoint(args.report, report)
            print(
                json.dumps({"prepared": run_id, "session_id": session["session_id"]}),
                flush=True,
            )
            registry.register(
                ProviderName.OPENAI,
                SeedProvider(file["file_id"]),
                allowed_models=frozenset({SEED_MODEL}),
            )
            binding = {"provider": "strata", "model": MODEL, "timeout_seconds": 600}
            seed_binding = AgentModelBinding(
                provider=ProviderName.OPENAI, model=SEED_MODEL, timeout_seconds=600
            )
            started = time.monotonic()
            async with register_seed(database_session_factory):
                app.dependency_overrides[get_provider_registry] = lambda: registry
                try:
                    with seed_first_turn(
                        GeneralAgentOrchestrator, run_id, seed_binding
                    ):
                        result = await request(
                            client,
                            "POST",
                            f"runs/{run_id}/agent-workflows",
                            json={
                                "workflow_name": "general_agent",
                                "workflow_version": "1.0.0",
                                "execution_profile": "general_tools_v1",
                                "review_policy": "always",
                                "role_bindings": {
                                    "controller": binding,
                                    "reviewer": binding,
                                },
                                "idempotency_key": str(uuid4()),
                                "stream": False,
                            },
                        )
                finally:
                    app.dependency_overrides.pop(get_provider_registry, None)
            report.update(
                status=result["status"],
                result=result,
                workflow_execution_id=result["workflow_execution_id"],
                elapsed_seconds=round(time.monotonic() - started, 3),
            )
            observed = await inspect(run_id)
            report.update(observed)
            async with database_session_factory() as db:
                seeds = list(
                    await db.scalars(
                        select(LlmModelAttempt.attempt_id).where(
                            LlmModelAttempt.run_id == run_id,
                            LlmModelAttempt.model == SEED_MODEL,
                        )
                    )
                )
            report["seed_attempt_ids"] = seeds
            real = [a for a in report["attempts"] if a["provider"] == "strata"]
            report["strata_known_usage"] = {
                key: sum(a[key] for a in real if a[key] is not None)
                for key in ("input_tokens", "output_tokens", "cached_tokens")
            }
            report["strata_unknown_usage_attempts"] = {
                key: sum(a[key] is None for a in real)
                for key in ("input_tokens", "output_tokens", "cached_tokens")
            }
            report["storage"] = await storage_evidence(run_id)
            report["execution_checkpoints"] = await execution_checkpoint_evidence(
                run_id
            )
            checkpoint(args.report, report)
            print(
                json.dumps(
                    {
                        "status": report["status"],
                        "seed_attempts": len(seeds),
                        "strata_attempts": len(real),
                        "elapsed_seconds": report["elapsed_seconds"],
                        "strata_known_usage": report["strata_known_usage"],
                    }
                ),
                flush=True,
            )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument(
        "--file", type=Path, default=Path("/Users/mith/Downloads/FULLTEXT03.pdf")
    )
    parser.add_argument("--allow-paid-strata", action="store_true")
    asyncio.run(run(parser.parse_args()))
