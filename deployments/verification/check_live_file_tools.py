"""Explicitly authorized, paid DeepSeek checks against a disposable LOCAL API.

Run with apps/api/.venv/bin/python. Set the database to the disposable Compose
project on 127.0.0.1:13306 and MinIO to 127.0.0.1:19000. Credentials are read by
Settings from apps/api/.env, never printed. --seed only registers the existing
configured model in an empty test database; it does not change endpoint URLs.
--fixtures must contain synthetic files from create_file_fixtures.py. Each run
creates two conversation turns and checks actual committed tools, not just text.
"""

import argparse
import asyncio
import hashlib
import json
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from nexuspilot_models.contracts import ProviderName
from sqlalchemy import select

from nexuspilot_api.core.config import get_settings
from nexuspilot_api.infrastructure.database import database_session_factory, engine
from nexuspilot_api.models import (
    LlmModelAttempt, LlmModelCatalogVersion, LlmModelToolCall,
    LlmRegisteredModel, User,
)
from nexuspilot_api.models.base import utc_now

MARKER = "FILE_RUNTIME_271828"


def validate_local_target(origin: str) -> None:
    """Refuse production, standard development ports and non-loopback targets."""
    settings = get_settings()
    database = urlsplit(settings.database_url)
    target = urlsplit(origin)
    if not (
        database.hostname == "127.0.0.1" and database.port == 13306
        and database.path == "/nexuspilot" and settings.minio_endpoint == "127.0.0.1:19000"
        and target.scheme == "http" and target.hostname == "127.0.0.1" and target.port == 18000
        and not settings.authentication_enabled and settings.environment == "development"
    ):
        raise RuntimeError("Refusing target outside the dedicated local verification instance")


async def seed_model(model: str) -> None:
    """Add a test capability budget without altering a database containing user data."""
    async with database_session_factory() as db:
        if await db.scalar(select(User.user_id).limit(1)):
            raise RuntimeError("Refusing to seed a database containing users")
        if await db.scalar(select(LlmRegisteredModel.model_id).limit(1)):
            raise RuntimeError("Refusing to replace existing model records")
        if model not in get_settings().deepseek_models.split(","):
            raise RuntimeError("Model is not in the local configured allowlist")
        db.add(LlmRegisteredModel(
            provider=ProviderName.DEEPSEEK, model_identifier=model,
            display_name="DeepSeek local file-tool verification",
            reasoning_supported_efforts_json=["minimal", "medium", "xhigh"],
            reasoning_supports_disable=True, reasoning_default_enabled=True,
            reasoning_default_effort="medium",
            reasoning_effort_mapping_json={"minimal": "high", "medium": "high", "xhigh": "max"},
            reasoning_profile_version="local-verification.v1",
        ))
        db.add(LlmModelCatalogVersion(
            provider="deepseek", model=model, catalog_version="local-live-file-tools",
            source="verification input budget, not an upstream context-window claim",
            confirmed_at=utc_now(), context_window=131072, supports_tools=True,
            supports_streaming=True, tokenizer_name="utf8_bytes_upper_bound",
            tokenizer_version="v1", capabilities_json={},
        ))
        await db.commit()


async def request_json(client, method: str, path: str, **kwargs):
    """Perform an actual local HTTP operation without logging authorization or raw errors."""
    reply = await client.request(method, path, **kwargs)
    if reply.status_code >= 400:
        raise RuntimeError(f"{method} {path} failed: HTTP {reply.status_code}")
    return reply.json()


async def stream_events(client, path: str, payload: dict) -> list[dict]:
    """Consume real HTTP SSE incrementally while printing only event names and sequence."""
    events = []
    async with client.stream("POST", path, json=payload) as response:
        if response.status_code >= 400:
            raise RuntimeError(f"Stream HTTP {response.status_code}")
        async for line in response.aiter_lines():
            if line.startswith("data: "):
                event = json.loads(line[6:])
                events.append(event)
                kind = event.get("type", event.get("event_type", "unknown"))
                if kind not in {"response.text.delta", "reasoning.raw.delta", "reasoning.summary.delta"}:
                    print(json.dumps({"event": kind, "sequence": event.get("sequence")}), flush=True)
    return events


async def inspect_run(run_id: str) -> dict:
    """Read actual MySQL attempts/tool results and return only bounded non-sensitive evidence."""
    async with database_session_factory() as db:
        attempts = list(await db.scalars(select(LlmModelAttempt).where(
            LlmModelAttempt.run_id == run_id,
        ).order_by(LlmModelAttempt.started_at)))
        calls = list(await db.scalars(select(LlmModelToolCall).join(LlmModelAttempt).where(
            LlmModelAttempt.run_id == run_id,
        ).order_by(LlmModelToolCall.started_at)))
        return {
            "run_id": run_id,
            "attempts": [{"id": a.attempt_id, "status": a.status.value,
                "input_tokens": a.input_tokens, "output_tokens": a.output_tokens,
                "reasoning_tokens": a.reasoning_tokens, "error_code": a.error_code,
                "context_build_id": a.context_build_id} for a in attempts],
            "tools": [{"id": c.tool_call_id, "name": c.tool_name, "status": c.status,
                "file_id": c.input_json.get("file_id"), "error_code": c.error_code,
                "contains_marker": MARKER in json.dumps(c.output_json),
            } for c in calls],
        }


async def check_mode(args, report: dict) -> None:
    """Upload DOCX then a new PDF on a continued turn and verify automatic tools in one mode."""
    settings = get_settings()
    async with httpx.AsyncClient(base_url=args.origin + "/api/v1/", timeout=660,
                                 headers={"X-API-Key": settings.api_key}) as client:
        owner = (await request_json(client, "GET", "auth/me"))["user_id"]
        capabilities = await request_json(client, "GET", "files/capabilities")
        if capabilities["executor_status"] != "available":
            raise RuntimeError("Local parser executor is not available")
        session = await request_json(client, "POST", "sessions", json={
            "user_id": owner, "title": f"Live DeepSeek file tools {args.mode} {uuid.uuid4().hex[:6]}",
        })
        report["session_id"] = session["session_id"]
        for number, extension in enumerate(("docx", "pdf")):
            filename = f"sample.{extension}"
            content = (args.fixtures / filename).read_bytes()
            reserved = await request_json(client, "POST", "files", json={
                "filename": filename, "size_bytes": len(content),
                "content_hash": hashlib.sha256(content).hexdigest(),
                "idempotency_key": str(uuid.uuid4()),
            })
            started = time.monotonic()
            uploaded = await request_json(client, "PUT", f"files/{reserved['file_id']}/content",
                                          content=content)
            assert uploaded["status"] == "ready", "File did not reach ready state"
            question = (
                f"请通过文件读取工具实际读取刚上传的 {filename}，找出中文文件读取测试后的标记。"
                "必须先调用工具核对原文，不要只引用对话中的预置摘要。只需给出标记和文件名，"
                "无需检索网络或编写代码。"
            )
            turn = await request_json(client, "POST", f"sessions/{session['session_id']}/turns",
                                      json={"user_id": owner, "content_text": question,
                                            "file_ids": [reserved["file_id"]],
                                            "idempotency_key": str(uuid.uuid4())})
            run_id = turn["run"]["run_id"]
            case = {"turn": number + 1, "filename": filename, "file_id": reserved["file_id"],
                    "run_id": run_id, "parse_seconds": round(time.monotonic() - started, 3)}
            report["cases"].append(case)
            print(json.dumps({"starting": args.mode, **case}), flush=True)
            started = time.monotonic()
            try:
                if args.mode == "quick":
                    # No tools/execution profile is supplied: the server must derive availability.
                    events = await stream_events(client, "responses", {
                        "run_id": run_id, "current_user_message_id": turn["message"]["message_id"],
                        "provider": "deepseek", "model": args.model, "input": question,
                        "stream": True, "timeout_seconds": 240,
                        "idempotency_key": str(uuid.uuid4()),
                    })
                    completed = [e for e in events if e.get("type") == "response.completed"]
                    assert len(completed) == 1, "Expected one final response.completed"
                    result = completed[0]["data"]["response"]
                    answer = result["output_text"]
                    await request_json(client, "POST", f"sessions/{session['session_id']}/messages",
                        json={"role": "assistant", "content_text": answer, "run_id": run_id,
                              "source_model_attempt_id": result["id"]})
                    case["sse_events"] = len(events)
                    case["stream_replay_attempt_id"] = events[0]["response_id"]
                else:
                    binding = {"provider": "deepseek", "model": args.model,
                               "structured_output_mode": "prompted_json", "timeout_seconds": 180}
                    events = await stream_events(client, f"runs/{run_id}/agent-workflows", {
                        "idempotency_key": str(uuid.uuid4()), "stream": True,
                        "role_bindings": {role: binding for role in ("controller", "planner", "reviewer")},
                    })
                    summary = await request_json(client, "GET", f"runs/{run_id}/agent-workflow")
                    result = await request_json(client, "GET",
                        f"agent-workflows/{summary['workflow_execution_id']}/result")
                    case["workflow_status"] = result["status"]
                    case["execution_profile"] = result["execution_profile"]
                    case["workflow_error"] = result.get("error")
                    case["sse_events"] = len(events)
                    assert result["status"] == "completed", "Agent workflow did not complete"
                    messages = await request_json(client, "GET",
                        f"sessions/{session['session_id']}/messages/latest?limit=100")
                    answer = next(m["content_text"] for m in reversed(messages["items"])
                                  if m["role"] == "assistant" and m["run_id"] == run_id)
                case["answer"] = answer
                assert MARKER in answer, "Final answer is missing the synthetic file marker"
            finally:
                case["elapsed_seconds"] = round(time.monotonic() - started, 3)
                case.update(await inspect_run(run_id))
            assert any(c["status"] == "completed" and c["contains_marker"]
                       and c["file_id"] == reserved["file_id"] for c in case["tools"]), \
                "No actual successful read of this turn's newly uploaded file"
            assert all(a["context_build_id"] for a in case["attempts"]), "Missing saved Context"
            case["status"] = "passed"
            print(json.dumps(case, ensure_ascii=False), flush=True)


async def main(args) -> int:
    """Run only the selected local paid check and write a sanitized, non-overwriting report."""
    validate_local_target(args.origin)
    report = {"mode": args.mode, "model": args.model, "cases": [], "status": "running"}
    try:
        if args.seed:
            await seed_model(args.model)
            print("Local live model registered; endpoint unchanged.")
            return 0
        await check_mode(args, report)
        report["status"] = "passed"
        return 0
    except Exception as error:
        report["status"] = "failed"
        report["error_type"] = type(error).__name__
        # Only controlled assertion messages are included; arbitrary library errors may hold secrets.
        if isinstance(error, (AssertionError, RuntimeError)):
            report["error"] = str(error)
        print(json.dumps({k: v for k, v in report.items() if k != "cases"}, ensure_ascii=False), flush=True)
        return 1
    finally:
        if not args.seed:
            args.output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            with args.output.open("x") as output:
                json.dump(report, output, ensure_ascii=False, indent=2)
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", action="store_true")
    parser.add_argument("--mode", choices=("quick", "agent"), default="quick")
    parser.add_argument("--origin", default="http://127.0.0.1:18000")
    parser.add_argument("--model", default="deepseek-v4-flash")
    parser.add_argument("--fixtures", type=Path)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    if not arguments.seed and (arguments.fixtures is None or arguments.output is None):
        parser.error("--fixtures and --output are required for a paid check")
    if arguments.output is not None and arguments.output.exists():
        parser.error("--output must be a new path; existing reports are never overwritten")
    raise SystemExit(asyncio.run(main(arguments)))
