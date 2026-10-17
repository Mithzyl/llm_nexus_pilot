"""One paid Strata attachment conversation against local API/MySQL/MinIO.

Run with API Python and PYTHONPATH=apps/api/src:packages/models/src, --output NEW_PATH.
Uses the existing loopback verification services and private apps/api/.env key.
Uploads synthetic TXT only; real platform tools must read its unpredictable marker.
No response-format overrides, simulated results, retries, migrations or deployment.
The platform's eight-round/600-second file-loop limits remain in force.
"""

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import time
import uuid

import check_live_worker_evidence as local
import httpx
from sqlalchemy import select, text

from nexuspilot_api.main import app
from nexuspilot_api.models import LlmContextSource


async def check(report):
    """Require actual tool evidence, restored answer and no automatic full-text injection."""
    marker = "ATTACHMENT-" + uuid.uuid4().hex
    content = f"合成附件验收\n本次核验编号：{marker}\n设备型号：NX-731\n".encode()
    started = time.monotonic()
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://127.0.0.1/api/v1/",
            headers={"X-API-Key": local.get_settings().api_key},
            timeout=660,
        ) as client,
    ):

        async def request(method, path, **kwargs):
            """Expose only HTTP status on failure, never arbitrary response secrets."""
            response = await client.request(method, path, **kwargs)
            assert response.is_success, (path, response.status_code)
            return response.json()

        owner = (await request("GET", "auth/me"))["user_id"]
        session = await request(
            "POST",
            "sessions",
            json={
                "user_id": owner,
                "title": "Strata attachment object-storage acceptance",
            },
        )
        report["session_id"] = session["session_id"]
        file = await request(
            "POST",
            "files",
            json={
                "filename": "strata-acceptance.txt",
                "size_bytes": len(content),
                "content_hash": hashlib.sha256(content).hexdigest(),
                "idempotency_key": str(uuid.uuid4()),
            },
        )
        uploaded = await request(
            "PUT", f"files/{file['file_id']}/content", content=content
        )
        assert uploaded["status"] == "ready"
        report["file_id"] = file["file_id"]
        question = "请实际调用附件读取工具，读取 strata-acceptance.txt，回答其中的核验编号和设备型号。"
        turn = await request(
            "POST",
            f"sessions/{session['session_id']}/turns",
            json={
                "user_id": owner,
                "content_text": question,
                "file_ids": [file["file_id"]],
                "idempotency_key": str(uuid.uuid4()),
            },
        )
        report["run_id"] = turn["run"]["run_id"]
        print(json.dumps({"started": report["run_id"]}), flush=True)
        try:
            result = await request(
                "POST",
                "responses",
                json={
                    "run_id": report["run_id"],
                    "current_user_message_id": turn["message"]["message_id"],
                    "provider": "strata",
                    "model": "tiel-coder-35b-a3b-ud-q4_k_xl",
                    "input": question,
                    "timeout_seconds": 600,
                    "idempotency_key": str(uuid.uuid4()),
                },
            )
            answer = result["output_text"]
            assert marker in answer and "NX-731" in answer, (
                "Missing actual attachment content"
            )
            message = await request(
                "POST",
                f"sessions/{session['session_id']}/messages",
                json={
                    "role": "assistant",
                    "content_text": answer,
                    "run_id": report["run_id"],
                    "source_model_attempt_id": result["id"],
                },
            )
            restored = await request("GET", f"messages/{message['message_id']}")
            assert restored["content_text"] == answer
            report["answer_restored"] = True
        finally:
            report["elapsed_seconds"] = round(time.monotonic() - started, 3)
            async with local.database_session_factory() as db:
                attempts = list(
                    await db.scalars(
                        select(local.LlmModelAttempt)
                        .where(local.LlmModelAttempt.run_id == report["run_id"])
                        .order_by(local.LlmModelAttempt.started_at)
                    )
                )
                report["attempts"] = [
                    {
                        "id": a.attempt_id,
                        "status": a.status.value,
                        "input_tokens": a.input_tokens,
                        "output_tokens": a.output_tokens,
                        "cached_tokens": a.cached_tokens,
                        "estimated_cost": str(a.estimated_cost),
                        "error_code": a.error_code,
                    }
                    for a in attempts
                ]
                calls = list(
                    await db.scalars(
                        select(local.LlmModelToolCall).where(
                            local.LlmModelToolCall.attempt_id.in_(
                                [a.attempt_id for a in attempts]
                            )
                        )
                    )
                )
                report["tools"] = [
                    {
                        "name": c.tool_name,
                        "status": c.status,
                        "file_id": c.input_json.get("file_id"),
                        "marker_in_result": marker in json.dumps(c.output_json),
                    }
                    for c in calls
                ]
                if report.get("answer_restored"):
                    assert any(
                        c["status"] == "completed"
                        and c["marker_in_result"]
                        and c["file_id"] == file["file_id"]
                        for c in report["tools"]
                    )
                    sources = list(
                        await db.scalars(
                            select(LlmContextSource).where(
                                LlmContextSource.context_build_id
                                == attempts[0].context_build_id
                            )
                        )
                    )
                    assert all(marker not in (s.content_text or "") for s in sources)
                    row = (
                        await db.execute(
                            text(
                                "SELECT content_text, body_ref_json FROM llm_messages WHERE message_id=:id"
                            ),
                            {"id": message["message_id"]},
                        )
                    ).one()
                    assert row.content_text is None and row.body_ref_json
                    report["no_full_text_in_initial_context"] = True
                    report["answer_body_in_minio"] = True
        report["status"] = "passed"


async def main(path):
    """Preserve a new sanitized report even on failure; never repeat the paid conversation."""
    report = {"status": "running", "provider": "strata", "mode": "quick_with_file"}
    try:
        await check(report)
    except Exception as error:
        report.update(status="failed", error_type=type(error).__name__)
        raise
    finally:
        await local.engine.dispose()
        with path.open("x") as output:
            json.dump(report, output, ensure_ascii=False, indent=2)
        print(json.dumps(report, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output already exists")
    asyncio.run(main(args.output))
