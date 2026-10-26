"""Explicit paid local web-tool checks using OpenCode Go and the configured search provider.

Start the current API on 127.0.0.1:8003 with inline execution and configured private keys.
From apps/api run .venv/bin/python -m nexuspilot_api.commands.verify_web_tools --run-paid.
Requires the existing nexuspilot-web local owner, MySQL and MinIO. Creates two new sessions;
never retries a scenario, changes model registration, or stores credentials/private text.
Writes metadata to output/security/web-tools-opencode-go-20261026.json after each scenario.
"""

import argparse
import asyncio
import json
import time
import uuid
from pathlib import Path

import httpx
from sqlalchemy import select

from nexuspilot_api.core.config import get_settings
from nexuspilot_api.infrastructure.database import database_session_factory, engine
from nexuspilot_api.infrastructure.object_storage import get_object_storage
from nexuspilot_api.models import LlmAgentWorkflowNodeExecution, LlmModelAttempt, LlmModelToolCall

REPORT = Path("../../output/security/web-tools-opencode-go-20261026.json")
PROMPT = (
    "联网工具验收：请实际调用一次web_search，"
    "查询site:support.google.com/mail Gmail create account，"
    "最多3条。根据返回结果用两句话说明可在哪里查看Gmail账号创建帮助，并给出来源链接。"
    "只搜索一次，不抓取网页、不使用其他工具、不创建子任务；若工具不可用或失败请明确说明。"
)


async def inspect_calls(run_id):
    """Read only this run's metadata and check tool-message persistence without exposing bodies."""
    async with database_session_factory() as db:
        db.info["authorized_user_id"] = "nexuspilot-web"
        attempts = list(
            await db.scalars(
                select(LlmModelAttempt)
                .where(LlmModelAttempt.run_id == run_id)
                .order_by(LlmModelAttempt.started_at)
            )
        )
        calls = list(
            await db.scalars(
                select(LlmModelToolCall)
                .join(LlmModelAttempt)
                .where(LlmModelAttempt.run_id == run_id)
                .order_by(LlmModelToolCall.started_at)
            )
        )
        fields = ("input_tokens", "output_tokens", "cached_tokens", "reasoning_tokens")
        return {
            "attempts": [
                {
                    "attempt_id": a.attempt_id,
                    "provider": a.provider,
                    "model": a.model,
                    "status": a.status.value,
                    **{f: getattr(a, f) for f in fields},
                }
                for a in attempts
            ],
            "usage_totals": {
                f: (
                    sum(getattr(a, f) for a in attempts)
                    if attempts and all(getattr(a, f) is not None for a in attempts)
                    else None
                )
                for f in fields
            },
            "tool_calls": [
                {
                    "tool_call_id": c.tool_call_id,
                    "tool_name": c.tool_name,
                    "status": c.status,
                    "error_code": c.error_code,
                    "separate_result_uri": c.result_uri is not None,
                    "web_hash_present": any(
                        k in ((c.output_json or {}).get("result") or {})
                        for k in ("source_hash", "parsed_hash")
                    ),
                    "excerpt_count": len(
                        ((c.output_json or {}).get("result") or {})
                        .get("data", {})
                        .get("blocks", [])
                    ),
                }
                for c in calls
            ],
        }


async def scenario(client, mode):
    """Execute exactly one new conversation turn and read its persisted answer again."""
    session = await client.post("/api/v1/sessions", json={"user_id": "nexuspilot-web"})
    session.raise_for_status()
    session_id = session.json()["session_id"]
    turn = await client.post(
        f"/api/v1/sessions/{session_id}/turns",
        json={
            "user_id": "nexuspilot-web",
            "content_text": PROMPT,
        },
    )
    turn.raise_for_status()
    turn = turn.json()
    run_id = turn["run"]["run_id"]
    record = {"mode": mode, "session_id": session_id, "run_id": run_id}
    started = time.monotonic()
    try:
        binding = {"provider": "opencode", "model": "deepseek-v4.1-flash"}
        if mode == "quick_reply":
            response = await client.post(
                "/api/v1/responses",
                json={
                    "run_id": run_id,
                    "current_user_message_id": turn["message"]["message_id"],
                    "input": PROMPT,
                    **binding,
                },
            )
        else:
            response = await client.post(
                f"/api/v1/runs/{run_id}/agent-workflows",
                json={
                    "workflow_name": "general_agent",
                    "workflow_version": "1.0.0",
                    "execution_profile": "general_tools_v1",
                    "review_policy": "always",
                    "idempotency_key": str(uuid.uuid4()),
                    "stream": False,
                    "role_bindings": {"controller": binding, "reviewer": binding},
                },
            )
        record["http_status"] = response.status_code
        if response.is_error:
            record["status"] = "http_error"
        else:
            body = response.json()
            record["status"] = body.get("status", "completed")
            record["final_text_present"] = bool(
                body.get("output_text") or (body.get("final_output") or {}).get("final_text")
            )
            if mode == "quick_reply" and body.get("output_text"):
                saved = await client.post(
                    f"/api/v1/sessions/{session_id}/messages",
                    json={
                        "role": "assistant",
                        "content_type": "text",
                        "content_text": body["output_text"],
                        "run_id": run_id,
                        "source_model_attempt_id": body["id"],
                    },
                )
                saved.raise_for_status()
                record["client_saved_answer"] = True
            if mode == "agent":
                workflow_id = body["workflow_execution_id"]
                record["workflow_id"] = workflow_id
                restored = await client.get(f"/api/v1/agent-workflows/{workflow_id}/result")
                record["result_restored"] = restored.status_code == 200 and bool(
                    (restored.json().get("final_output") or {}).get("final_text")
                )
                replay = await client.get(f"/api/v1/agent-workflows/{workflow_id}/events")
                record["events_replayed"] = replay.status_code == 200 and "data:" in replay.text
                record["web_source_reference_count"] = json.dumps(body).count('"web_result_id"')
            restored = await client.get(f"/api/v1/sessions/{session_id}/messages/latest")
            record["messages_restored"] = (
                restored.status_code == 200 and '"assistant"' in restored.text
            )
    except (httpx.HTTPError, KeyError, ValueError) as error:
        record["verification_error_type"] = type(error).__name__
    record["elapsed_seconds"] = round(time.monotonic() - started, 3)
    record.update(await inspect_calls(run_id))
    return record


async def main():
    """Require an explicit paid-run switch and persist each result before the next scenario."""
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--run-paid", action="store_true")
    action.add_argument("--verify-saved", action="store_true")
    args = parser.parse_args()
    report = {
        "date": "2026-10-26",
        "environment": "local HTTP API, real MySQL/MinIO",
        "model_provider": "opencode",
        "model": "deepseek-v4.1-flash",
        "search_provider": "deepseek_primary_tavily_backup",
        "runs": [],
    }
    try:
        async with httpx.AsyncClient(
            base_url="http://127.0.0.1:8003",
            timeout=600,
            headers={"X-API-Key": get_settings().api_key},
        ) as client:
            if args.verify_saved:
                report = json.loads(REPORT.read_text())
                for record in report["runs"]:
                    await verify_saved(client, record)
                REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
                print(json.dumps(report, ensure_ascii=False), flush=True)
                return
            for mode in ("quick_reply", "agent"):
                record = await scenario(client, mode)
                report["runs"].append(record)
                REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
                print(json.dumps(record, ensure_ascii=False), flush=True)
    finally:
        await engine.dispose()


async def verify_saved(client, record):
    """Recheck without model calls and finish the client's quick-answer save if missing."""
    session_id = record["session_id"]
    restored = await client.get(f"/api/v1/sessions/{session_id}/messages/latest")
    restored.raise_for_status()
    if record["mode"] == "quick_reply" and not any(
        item["role"] == "assistant" for item in restored.json()["items"]
    ):
        async with database_session_factory() as db:
            attempt = await db.get(LlmModelAttempt, record["attempts"][-1]["attempt_id"])
            stored = await get_object_storage().open_object(attempt.raw_response_uri)
            raw = json.loads(await asyncio.to_thread(b"".join, stored.chunks))
            body = raw.get("nexus_completed_response")
            if not body and raw.get("choices"):
                # This verification uses the inspected OpenAI Chat Completions response shape.
                body = {"output_text": raw["choices"][0].get("message", {}).get("content")}
            if not body or not body.get("output_text"):
                record["saved_response_recovery"] = "canonical_response_unavailable"
                return
            saved = await client.post(
                f"/api/v1/sessions/{session_id}/messages",
                json={
                    "role": "assistant",
                    "content_type": "text",
                    "content_text": body["output_text"],
                    "run_id": record["run_id"],
                    "source_model_attempt_id": attempt.attempt_id,
                },
            )
            saved.raise_for_status()
            record["client_saved_answer"] = True
            record.pop("saved_response_recovery", None)
            record["verification_note"] = (
                "Initial script omitted the Web client's save; recovered from the saved response "
                "without a new model call."
            )
    restored = await client.get(f"/api/v1/sessions/{session_id}/messages/latest")
    restored.raise_for_status()
    record["messages_restored"] = any(
        item["role"] == "assistant" and item["content_text"] for item in restored.json()["items"]
    )
    record["saved_recheck_without_model_calls"] = True
    if record["mode"] == "agent":
        async with database_session_factory() as db:
            db.info["authorized_user_id"] = "nexuspilot-web"
            candidate = await db.scalar(
                select(LlmAgentWorkflowNodeExecution).where(
                    LlmAgentWorkflowNodeExecution.run_id == record["run_id"],
                    LlmAgentWorkflowNodeExecution.output_type == "general_candidate",
                )
            )
            unit = next(
                (u for u in candidate.output_json["units"] if u["citation_status"] == "present"),
                None,
            )
            if unit:
                sources = await client.get(
                    f"/api/v1/agent-workflows/{record['workflow_id']}/nodes/"
                    f"{candidate.node_execution_id}/claim-sources",
                    params={"claim_key": unit["unit_id"]},
                )
                record["source_read_http_status"] = sources.status_code
                record["source_url_title_excerpt_restored"] = sources.status_code == 200 and any(
                    b.get("url") and b.get("title") and b.get("text")
                    for s in sources.json()["sources"]
                    for b in s["blocks"]
                )


if __name__ == "__main__":
    asyncio.run(main())
