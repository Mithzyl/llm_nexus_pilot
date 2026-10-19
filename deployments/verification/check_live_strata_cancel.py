"""Paid local Strata cancellation acceptance; never deploy or automatically retry.

Run with API Python and PYTHONPATH=apps/api/src:packages/models/src, --mode quick|agent
and --output NEW.json. Uses the same local services and FULLTEXT03.pdf as
check_live_strata_long_files.py. Cancels only the newly created verification Run,
after its first provider attempt remains STARTED for two seconds. The observation
deadline is for this test, not a model timeout policy. Unknown usage stays unknown.
"""

import argparse
import asyncio
import json
import time
from pathlib import Path

import httpx
from sqlalchemy import select

from check_live_strata_long_files import inspect, main as verify
from nexuspilot_api.core.config import get_settings
from nexuspilot_api.infrastructure.database import database_session_factory, engine
from nexuspilot_api.models import LlmAgentWorkflowExecution, LlmModelAttempt, LlmRun


async def verify_cancellation(output: Path, mode: str):
    """Observe our own dispatch, cancel via the public API, then verify local execution stops."""
    if output.exists():
        raise RuntimeError("Use a new report path")
    run_id = None
    execution = asyncio.create_task(verify(output, mode, True))
    try:
        async with asyncio.timeout(60):
            while True:
                if execution.done():
                    await execution
                    raise RuntimeError("Run ended before cancellation could be tested")
                report = json.loads(output.read_text()) if output.exists() else {}
                cases = report.get("cases", [])
                if cases:
                    run_id = cases[0]["run_id"]
                    async with database_session_factory() as db:
                        attempt = await db.scalar(select(LlmModelAttempt).where(
                            LlmModelAttempt.run_id == run_id,
                            LlmModelAttempt.status == "started"))
                    if attempt is not None:
                        break
                await asyncio.sleep(0.25)
        await asyncio.sleep(2)
        async with database_session_factory() as db:
            current = await db.get(LlmModelAttempt, attempt.attempt_id)
            if current.status.value != "started":
                raise RuntimeError("Provider already returned; in-flight cancel not demonstrated")
            workflow = await db.scalar(select(LlmAgentWorkflowExecution).where(
                LlmAgentWorkflowExecution.run_id == run_id))
        path = (f"agent-workflows/{workflow.workflow_execution_id}/cancel"
                if mode == "agent" else f"runs/{run_id}/cancel")
        started = time.monotonic()
        async with httpx.AsyncClient(base_url="http://127.0.0.1:8000/api/v1/", timeout=15,
                headers={"X-API-Key": get_settings().api_key}) as client:
            response = await client.post(path)
            response.raise_for_status()
        acknowledgement_seconds = time.monotonic() - started
        await asyncio.wait_for(asyncio.shield(execution), timeout=15)
        stopped_seconds = time.monotonic() - started
        first = await inspect(run_id)
        await asyncio.sleep(3)
        after = await inspect(run_id)
        async with database_session_factory() as db:
            run = await db.get(LlmRun, run_id)
        local_stopped = (run.status.value == "cancelled"
                         and len(after["attempts"]) == len(first["attempts"])
                         and all(a["status"] != "started" for a in after["attempts"]))
        report = json.loads(output.read_text())
        report["cancel_verification"] = {
            "passed": local_stopped, "run_status": run.status.value,
            "acknowledgement_seconds": round(acknowledgement_seconds, 3),
            "local_request_finished_seconds": round(stopped_seconds, 3),
            "no_new_attempts_observation_seconds": 3,
            "attempt_count": len(after["attempts"]),
            "remote_generation_and_billing_stopped": "unknown",
        }
        report["cases"][0].update(after)
        report["cases"][0]["status"] = run.status.value
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print(json.dumps(report["cancel_verification"]), flush=True)
        assert local_stopped
    finally:
        if not execution.done():
            # A failed test must not leave its paid Run running after the observer exits.
            if run_id is not None:
                async with database_session_factory() as db:
                    workflow = await db.scalar(select(LlmAgentWorkflowExecution).where(
                        LlmAgentWorkflowExecution.run_id == run_id))
                path = (f"agent-workflows/{workflow.workflow_execution_id}/cancel"
                        if workflow is not None else f"runs/{run_id}/cancel")
                async with httpx.AsyncClient(
                    base_url="http://127.0.0.1:8000/api/v1/", timeout=15,
                    headers={"X-API-Key": get_settings().api_key},
                ) as client:
                    cleanup = await client.post(path)
                    if cleanup.status_code not in {200, 409}:
                        cleanup.raise_for_status()
            execution.cancel()
        await asyncio.gather(execution, return_exceptions=True)
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--mode", required=True, choices=["quick", "agent"])
    args = parser.parse_args()
    asyncio.run(verify_cancellation(args.output, args.mode))
