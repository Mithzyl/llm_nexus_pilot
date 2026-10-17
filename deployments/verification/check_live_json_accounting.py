"""One paid DeepSeek JSON check through the local API, MySQL and MinIO.

Run with apps/api/.venv/bin/python and --output NEW_PATH after starting the
isolated verification API on 18000. Uses synthetic facts and a system JSON
example, never Strata, deployment, credentials in reports or automatic retries.
"""

import argparse
import asyncio
import json
import time
import uuid
import sys
from pathlib import Path

import check_live_worker_evidence as local
import httpx
from sqlalchemy import select


async def check(report, minimal):
    """Record actual completion, persisted usage and audit availability even on JSON rejection."""
    async with local.database_session_factory() as db:
        endpoint = await db.scalar(
            select(local.LlmProviderEndpoint).where(
                local.LlmProviderEndpoint.provider == "deepseek",
                local.LlmProviderEndpoint.enabled.is_(True),
            )
        )
        if endpoint is None or endpoint.base_url.rstrip("/") not in {
            "https://api.deepseek.com",
            "https://api.deepseek.com/v1",
        }:
            raise RuntimeError("Expected official DeepSeek URL")
    prompt = 'Return only JSON {"ok":true}' if minimal else "The test color is blue."
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1:18000/api/v1/",
        timeout=150,
        headers={"X-API-Key": local.get_settings().api_key},
    ) as client:
        owner = (await local.request_json(client, "GET", "auth/me"))["user_id"]
        session = await local.request_json(
            client,
            "POST",
            "sessions",
            json={
                "user_id": owner,
                "title": "Synthetic JSON accounting check",
            },
        )
        turn = await local.request_json(
            client,
            "POST",
            f"sessions/{session['session_id']}/turns",
            json={
                "user_id": owner,
                "content_text": prompt,
                "idempotency_key": str(uuid.uuid4()),
            },
        )
        report["run_id"] = run_id = turn["run"]["run_id"]
        started = time.monotonic()
        response = await client.post(
            "responses",
            json={
                "run_id": run_id,
                "provider": "deepseek",
                "model": "deepseek-v4-flash",
                "current_user_message_id": turn["message"]["message_id"],
                "input": prompt,
                "instructions": None
                if minimal
                else 'Extract the test color. Output only a json object. Example input: The test color is red. Example JSON output: {"color":"red"}.',
                "json_object_output": True,
                "reasoning": {"enabled": False},
                "max_output_tokens": 1024,
                "timeout_seconds": 120,
                "idempotency_key": str(uuid.uuid4()),
            },
        )
        report.update(
            seconds=round(time.monotonic() - started, 3),
            http_status=response.status_code,
        )
        result = response.json()
        report["error"] = result.get("error")
        report["output_text"] = result.get("output_text")
        report["json_passed"] = response.is_success and result.get(
            "structured_output"
        ) == ({"ok": True} if minimal else {"color": "blue"})
        async with local.database_session_factory() as db:
            attempts = list(
                await db.scalars(
                    select(local.LlmModelAttempt).where(
                        local.LlmModelAttempt.run_id == run_id
                    )
                )
            )
            report["attempts"] = []
            for attempt in attempts:
                item = {
                    key: getattr(attempt, key)
                    for key in (
                        "attempt_id",
                        "status",
                        "input_tokens",
                        "output_tokens",
                        "cached_tokens",
                        "estimated_cost",
                        "latency_ms",
                        "error_code",
                    )
                }
                if attempt.raw_response_uri:
                    stored = await local.ObjectStorage().open_object(
                        attempt.raw_response_uri
                    )
                    raw = json.loads(b"".join(stored.chunks))
                    item["raw_usage"] = raw.get("usage")
                    item["synthetic_output"] = (
                        (raw.get("choices") or [{}])[0]
                        .get("message", {})
                        .get("content")
                    )
                    item["audit_readable"] = True
                    usage = raw.get("usage") or {}
                    item["usage_matches_audit"] = (
                        attempt.input_tokens == usage.get("prompt_tokens")
                        and attempt.output_tokens == usage.get("completion_tokens")
                        and attempt.input_tokens is not None
                        and attempt.output_tokens is not None
                    )
                report["attempts"].append(item)
        report["accounting_passed"] = len(attempts) == 1 and all(
            item.get("usage_matches_audit") for item in report["attempts"]
        )


async def main(output, minimal):
    """Save fresh evidence, distinguish JSON success from accounting success, and close MySQL."""
    report = {
        "provider": "deepseek",
        "model": "deepseek-v4-flash",
        "minimal_prompt": minimal,
    }
    try:
        await check(report, minimal)
    except Exception as exc:
        report["check_error"] = type(exc).__name__
    finally:
        await local.engine.dispose()
        with output.open("x") as file:
            json.dump(report, file, ensure_ascii=False, indent=2, default=str)
        print(
            json.dumps(
                {k: v for k, v in report.items() if k != "attempts"}, ensure_ascii=False
            ),
            flush=True,
        )
    return report.get("json_passed") and report.get("accounting_passed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--minimal", action="store_true")
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Refusing to overwrite evidence")
    sys.exit(0 if asyncio.run(main(args.output, args.minimal)) else 1)
