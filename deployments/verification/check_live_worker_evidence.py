"""Paid LOCAL DeepSeek evidence check; starts its own API, uses existing test MySQL/MinIO.

Run with apps/api/.venv/bin/python and --output NEW_PATH. Only the dedicated
loopback ports 13306/19000/18000 are used. Requires the seeded local verification
database, no migrations or seed mutations are performed. API credentials and
DeepSeek Key stay in apps/api/.env. No server deployment or production data access.
"""

import argparse
import asyncio
import hashlib
import json
import os
import socket
from pathlib import Path
import subprocess
import sys
import uuid

os.environ.update(
    {
        "NEXUSPILOT_DATABASE_URL": "mysql+aiomysql://nexuspilot:nexuspilot@127.0.0.1:13306/nexuspilot",
        "NEXUSPILOT_MINIO_ENDPOINT": "127.0.0.1:19000",
        "NEXUSPILOT_MINIO_ACCESS_KEY": "nexuspilot",
        "NEXUSPILOT_MINIO_SECRET_KEY": "local-development-secret-change-me",
        "NEXUSPILOT_MINIO_BUCKET": "nexuspilot-evidence-verification",
        "NEXUSPILOT_MINIO_SECURE": "false",
        "NEXUSPILOT_ENVIRONMENT": "development",
        "NEXUSPILOT_AUTHENTICATION_ENABLED": "false",
        "NEXUSPILOT_MODEL_MAX_RETRIES": "0",
    }
)

import httpx  # noqa: E402
from sqlalchemy import select  # noqa: E402
from check_live_file_tools import request_json, validate_local_target  # noqa: E402
from nexuspilot_api.core.config import get_settings  # noqa: E402
from nexuspilot_api.infrastructure.database import database_session_factory, engine  # noqa: E402
from nexuspilot_api.infrastructure.object_storage import ObjectStorage  # noqa: E402
from nexuspilot_api.models import (  # noqa: E402
    LlmAgentHandoff,
    LlmAgentWorkflowNodeExecution,
    LlmModelAttempt,
    LlmModelToolCall,
    LlmProviderEndpoint,
    UserFile,
)
from nexuspilot_api.services.worker_evidence_service import WorkerEvidenceService  # noqa: E402


async def inspect(run_id, report):
    """Verify committed SQL references and hash-checked MinIO originals without printing secrets."""
    storage = ObjectStorage()
    async with database_session_factory() as db:
        nodes = list(
            await db.scalars(
                select(LlmAgentWorkflowNodeExecution).where(
                    LlmAgentWorkflowNodeExecution.run_id == run_id
                )
            )
        )
        attempts = list(
            await db.scalars(
                select(LlmModelAttempt).where(LlmModelAttempt.run_id == run_id)
            )
        )
        calls = list(
            await db.scalars(
                select(LlmModelToolCall)
                .join(LlmModelAttempt)
                .where(LlmModelAttempt.run_id == run_id)
            )
        )
        report["attempts"] = [
            {
                "id": a.attempt_id,
                "task_id": a.task_id,
                "status": a.status.value,
                "error_code": a.error_code,
                "input_tokens": a.input_tokens,
                "output_tokens": a.output_tokens,
            }
            for a in attempts
        ]
        report["tools"] = [
            {
                "id": c.tool_call_id,
                "name": c.tool_name,
                "status": c.status,
                "arguments": c.input_json,
            }
            for c in calls
        ]
        report["nodes"] = [
            {"key": n.node_key, "status": n.status.value, "output": n.output_json}
            for n in nodes
        ]
        workers = [n for n in nodes if n.output_type == "agent_model_execution"]
        reviewer = next((n for n in nodes if n.node_key == "independent_review"), None)
        handoffs = list(
            await db.scalars(
                select(LlmAgentHandoff).where(LlmAgentHandoff.run_id == run_id)
            )
        )
        assert workers and handoffs and reviewer, "Missing completed workflow evidence"
        for handoff in handoffs:
            worker = next(n for n in workers if n.task_id == handoff.task_id)
            claims = worker.output_json["structured_output"]["claims"]
            assert claims, "Worker supplied no claims"
            assert handoff.schema_version == "agent_handoff.v2"
            assert handoff.handoff_json["worker_evidence"]["claims"] == claims
            assert all(
                ref["block_ids"] == ["line:1"]
                for claim in claims
                for ref in claim["evidence_refs"]
            ), "Worker did not restrict citations to the first block"
        report["handoff_matches_worker"] = True
        originals = await WorkerEvidenceService(db, storage).for_node(reviewer)
        report["resolved_claims"] = originals
        assert originals and originals[0]["claims"], "Original claim sources missing"
        assert all(
            source["read_scope"]["has_more"] is True
            for bundle in originals
            for claim in bundle["claims"]
            for source in claim["sources"]
        ), "Worker source scope did not preserve the unread second block"
        file = await db.get(UserFile, report["file_id"])
        assert await storage.verify_object(file.parsed_uri, file.parsed_hash)
        report["minio_parse_hash_verified"] = True
        review_attempts = [a for a in attempts if a.task_id == reviewer.task_id]
        review_calls = [
            c for c in calls if c.attempt_id in {a.attempt_id for a in review_attempts}
        ]
        assert any(
            c.status == "completed"
            and c.tool_name == "read_file_content"
            and c.input_json.get("offset") == 1
            for c in review_calls
        ), "Reviewer did not actually perform a supplementary read"
        request_has_original = False
        for attempt in review_attempts:
            if attempt.raw_request_uri:
                raw = await storage.open_object(attempt.raw_request_uri)
                payload = b"".join(raw.chunks).decode()
                request_has_original |= (
                    "authorized_claim_sources" in payload and "X100" in payload
                )
        assert request_has_original, (
            "Stored reviewer request does not contain original claim text"
        )
        report["reviewer_request_contains_original"] = True
        assessments = reviewer.output_json.get("claim_assessments", [])
        assert assessments, "Reviewer assessments missing"
        assert "X200" in json.dumps(reviewer.output_json), (
            "Reviewer missed the final model"
        )
        report["assessments"] = assessments


async def check(report):
    """Submit one synthetic two-block task; do not retry a failed paid workflow."""
    origin = "http://127.0.0.1:18000"
    validate_local_target(origin)
    settings = get_settings()
    async with database_session_factory() as db:
        endpoint = await db.scalar(
            select(LlmProviderEndpoint).where(
                LlmProviderEndpoint.provider == "deepseek",
                LlmProviderEndpoint.enabled.is_(True),
            )
        )
        assert endpoint and endpoint.base_url == "https://api.deepseek.com"
    async with httpx.AsyncClient(
        base_url=origin + "/api/v1/",
        timeout=660,
        headers={"X-API-Key": settings.api_key},
    ) as client:
        for _ in range(60):
            try:
                me = await request_json(client, "GET", "auth/me")
                break
            except httpx.ConnectError:
                await asyncio.sleep(0.5)
        else:
            raise RuntimeError("Local API did not start")
        session = await request_json(
            client,
            "POST",
            "sessions",
            json={
                "user_id": me["user_id"],
                "title": "Synthetic worker evidence verification",
            },
        )
        report["session_id"] = session["session_id"]
        content = (
            "Preliminary inventory: equipment model X100; pending independent review.\n"
            "Final signed inventory: equipment model X200; supersedes preliminary X100."
        ).encode()
        file = await request_json(
            client,
            "POST",
            "files",
            json={
                "filename": "equipment-review.txt",
                "size_bytes": len(content),
                "content_hash": hashlib.sha256(content).hexdigest(),
                "idempotency_key": str(uuid.uuid4()),
            },
        )
        report["file_id"] = file["file_id"]
        await request_json(
            client, "PUT", f"files/{file['file_id']}/content", content=content
        )
        question = (
            "这是分阶段文件审核测试，请严格分工：Controller 只规划一个 planner 工作任务，"
            "该 Worker 用 read_file_content(offset=0,limit=1) 只读附件首个块，"
            "提交第一段的初步设备型号 claim 和真实引用，明确是初步记录、不是最终定论，"
            "不要读第二块，留给独立审核。Reviewer 必须独立调用 read_file_content"
            "(offset=1,limit=1) 补读后续最终签字记录，比较型号与初步记录，"
            "对 Worker 的每条 claim 逐条审核，保留实际原文引用，指出最终型号。"
            "不要创建额外工作任务，也不要将只读第一段解释为文件没有后文。"
        )
        turn = await request_json(
            client,
            "POST",
            f"sessions/{session['session_id']}/turns",
            json={
                "user_id": me["user_id"],
                "content_text": question,
                "file_ids": [file["file_id"]],
                "idempotency_key": str(uuid.uuid4()),
            },
        )
        run_id = report["run_id"] = turn["run"]["run_id"]
        print(json.dumps({"started_run": run_id}), flush=True)
        binding = {
            "provider": "deepseek",
            "model": "deepseek-v4-flash",
            "structured_output_mode": "prompted_json",
            "timeout_seconds": 180,
        }
        result = await request_json(
            client,
            "POST",
            f"runs/{run_id}/agent-workflows",
            json={
                "idempotency_key": str(uuid.uuid4()),
                "max_model_calls": 12,
                "role_bindings": {
                    r: binding for r in ("controller", "planner", "reviewer")
                },
            },
        )
        report["workflow_status"] = result["status"]
        report["workflow_error"] = result.get("error")
        await inspect(run_id, report)
        assert result["status"] == "completed", "Workflow did not complete"


async def run_check(report):
    """Release the MySQL connection pool before closing the verification event loop."""
    try:
        await check(report)
    finally:
        await engine.dispose()


def main():
    """Start only a local API and always stop it; preserve a private report, even on failure."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output already exists")
    args.output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 18000))
    report = {"status": "running", "model": "deepseek-v4-flash"}
    with args.output.with_suffix(".api.log").open("x") as log:
        api = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "nexuspilot_api.main:app",
                "--app-dir",
                "apps/api/src",
                "--host",
                "127.0.0.1",
                "--port",
                "18000",
            ],
            stdout=log,
            stderr=log,
        )
        try:
            asyncio.run(run_check(report))
            report["status"] = "passed"
        except Exception as exc:
            report["status"] = "failed"
            report["error_type"] = type(exc).__name__
            if isinstance(exc, (AssertionError, RuntimeError)):
                report["error"] = str(exc)
        finally:
            api.terminate()
            api.wait(timeout=15)
            with args.output.open("x") as output:
                json.dump(report, output, ensure_ascii=False, indent=2, default=str)
            print(
                json.dumps(
                    {
                        k: v
                        for k, v in report.items()
                        if k
                        in {
                            "status",
                            "run_id",
                            "error",
                            "error_type",
                            "workflow_status",
                        }
                    }
                ),
                flush=True,
            )
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
