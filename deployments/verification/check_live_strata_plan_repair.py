"""Paid local Strata comparison and DeepSeek plan-repair verification.

Run with apps/api/.venv/bin/python, --mode seed|quick|agent|repair|strata-repair|inspect and a fresh
--output path. Reuses check_live_worker_evidence's isolated MySQL/MinIO settings.
Start a local API on 18000 with those settings after seed. Never deploys or
migrates. Seed adds only Strata endpoint/model records, refusing conflicting URLs.
Reports contain synthetic prompts, timings, usage and IDs, never credentials.
The inspect mode requires --run-id and makes only local read requests. The repair
mode uses DeepSeek; strata-repair uses Strata, including every correction attempt.
"""

import argparse
import asyncio
import json
import time
import uuid
from pathlib import Path

import check_live_worker_evidence as local
import httpx
from sqlalchemy import select

from nexuspilot_api.models import LlmModelCatalogVersion, LlmRegisteredModel
from nexuspilot_api.models.base import utc_now

MODEL = "tiel-coder-35b-a3b-ud-q4_k_xl"
URL = "https://mithfusion.site/v1"


async def seed():
    """Register the requested Strata endpoint in the dedicated local verification database."""
    async with local.database_session_factory() as db:
        endpoint = await db.scalar(
            select(local.LlmProviderEndpoint).where(
                local.LlmProviderEndpoint.provider == "strata",
                local.LlmProviderEndpoint.enabled.is_(True),
            )
        )
        if endpoint and endpoint.base_url != URL:
            raise RuntimeError("Conflicting Strata endpoint; no settings changed")
        if not endpoint:
            db.add(
                local.LlmProviderEndpoint(provider="strata", base_url=URL, enabled=True)
            )
        model = await db.scalar(
            select(LlmRegisteredModel).where(
                LlmRegisteredModel.provider == "strata",
                LlmRegisteredModel.model_identifier == MODEL,
            )
        )
        if not model:
            db.add(
                LlmRegisteredModel(
                    provider="strata",
                    model_identifier=MODEL,
                    display_name="Strata Tiel Coder 35B",
                    reasoning_profile_version="strata.provider-default.v1",
                )
            )
        catalog = await db.scalar(
            select(LlmModelCatalogVersion).where(
                LlmModelCatalogVersion.provider == "strata",
                LlmModelCatalogVersion.model == MODEL,
            )
        )
        if not catalog:
            db.add(
                LlmModelCatalogVersion(
                    provider="strata",
                    model=MODEL,
                    catalog_version="strata.local-verification.v1",
                    source="GET /v1/models meta.n_ctx; text-only local acceptance configuration",
                    confirmed_at=utc_now(),
                    context_window=163840,
                    supports_streaming=True,
                    supports_tools=False,
                    supports_structured_output=False,
                    tokenizer_name="utf8_bytes_upper_bound",
                    tokenizer_version="v1",
                    capabilities_json={},
                )
            )
        await db.commit()


async def inspect_attempts(run_id):
    """Read persisted attempt usage and verify corresponding MinIO response objects exist."""
    async with local.database_session_factory() as db:
        attempts = list(
            await db.scalars(
                select(local.LlmModelAttempt)
                .where(
                    local.LlmModelAttempt.run_id == run_id,
                )
                .order_by(local.LlmModelAttempt.started_at)
            )
        )
        storage = local.ObjectStorage()
        result = []
        for attempt in attempts:
            response_bytes = None
            if attempt.raw_response_uri:
                # Use the same storage URI resolver as the application's evidence reader.
                obj = await storage.open_object(attempt.raw_response_uri)
                response_bytes = sum(len(chunk) for chunk in obj.chunks)
            result.append(
                {
                    key: getattr(attempt, key)
                    for key in (
                        "attempt_id",
                        "agent_role",
                        "status",
                        "input_tokens",
                        "output_tokens",
                        "cached_tokens",
                        "reasoning_tokens",
                        "latency_ms",
                        "error_code",
                        "started_at",
                        "completed_at",
                        "first_visible_token_at",
                        "estimated_cost",
                    )
                }
                | {"raw_response_bytes": response_bytes}
            )
        return result


async def check(mode, report):
    """Submit one synthetic request, measure wall time and retain durable result/statistics."""
    question = "请用两句话解释 Python 列表与元组的区别，不用工具，不创建额外任务。"
    if mode in {"repair", "strata-repair"}:
        question = (
            "这是计划校验的受控测试。目标是解释 Python 列表与元组的区别。"
            "初次计划请列两个 planner 子任务，一个说明列表一个说明元组，"
            "两项 task_key 都填写 compare；不要创建依赖。"
            "如果平台返回校验失败，请按校验反馈修改 task_key 再完成任务。"
            "不要创建 Controller 或 Reviewer 任务，它们由平台执行。"
        )
        task = {
            "task_key": "compare",
            "task_type": "analysis",
            "title": "Explain list",
            "objective": "Explain Python lists in one sentence",
            "assigned_role": "planner",
            "required_capabilities": ["text_analysis"],
            "expected_output_type": "agent_handoff",
            "completion_criteria": ["One accurate sentence"],
            "max_model_calls": 1,
        }
        fixture = {
            "decision_summary": "Controlled duplicate-key acceptance sample",
            "tasks": [
                task,
                {
                    **task,
                    "title": "Explain tuple",
                    "objective": "Explain Python tuples",
                },
            ],
            "dependencies": [],
            "review_policy": "always",
            "known_risks": [],
            "unknowns": [],
        }
        question += (
            "这是故障输入验收，不是正常任务规划。首次 Controller 必须逐字段原样返回下述 JSON；"
            "不要提前修复重复键，由平台检测后返回你修正。收到 plan_repair 时再修正，之后按正常流程执行。"
            + json.dumps(fixture, ensure_ascii=False)
        )
    provider, model = (
        ("deepseek", "deepseek-v4-flash") if mode == "repair" else ("strata", MODEL)
    )
    report.update(provider=provider, model=model, question=question)
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1:18000/api/v1/",
        timeout=660,
        headers={"X-API-Key": local.get_settings().api_key},
    ) as client:
        owner = (await local.request_json(client, "GET", "auth/me"))["user_id"]
        session = await local.request_json(
            client,
            "POST",
            "sessions",
            json={
                "user_id": owner,
                "title": f"Local {mode} acceptance",
            },
        )
        report["session_id"] = session["session_id"]
        turn = await local.request_json(
            client,
            "POST",
            f"sessions/{session['session_id']}/turns",
            json={
                "user_id": owner,
                "content_text": question,
                "idempotency_key": str(uuid.uuid4()),
            },
        )
        run_id = report["run_id"] = turn["run"]["run_id"]
        print(json.dumps({"started": mode, "run_id": run_id}), flush=True)
        started = time.monotonic()
        if mode == "quick":
            async with client.stream(
                "POST",
                "responses",
                json={
                    "run_id": run_id,
                    "current_user_message_id": turn["message"]["message_id"],
                    "provider": provider,
                    "model": model,
                    "input": question,
                    "stream": True,
                    "timeout_seconds": 300,
                    "idempotency_key": str(uuid.uuid4()),
                },
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    event = json.loads(line[6:])
                    if event.get("type") == "response.text.delta":
                        report.setdefault(
                            "first_text_seconds", round(time.monotonic() - started, 3)
                        )
                    if event.get("type") == "response.completed":
                        report["response"] = event["data"]["response"]
                    if event.get("type") == "response.failed":
                        report["failure"] = event["data"]
        else:
            binding = {
                "provider": provider,
                "model": model,
                "structured_output_mode": "prompted_json",
                "timeout_seconds": 240,
            }
            report["workflow"] = await local.request_json(
                client,
                "POST",
                f"runs/{run_id}/agent-workflows",
                json={
                    "workflow_version": "1.1.0",
                    **({"reasoning": {"enabled": False}} if mode == "repair" else {}),
                    "idempotency_key": str(uuid.uuid4()),
                    "max_model_calls": 16,
                    "role_bindings": {
                        role: binding for role in ("controller", "planner", "reviewer")
                    },
                },
            )
        report["elapsed_seconds"] = round(time.monotonic() - started, 3)
        report["attempts"] = await inspect_attempts(run_id)
        report["usage_complete"] = all(
            a["input_tokens"] is not None and a["output_tokens"] is not None
            for a in report["attempts"]
        )
        report["known_input_tokens"] = sum(
            a["input_tokens"] or 0 for a in report["attempts"]
        )
        report["known_output_tokens"] = sum(
            a["output_tokens"] or 0 for a in report["attempts"]
        )
        report["input_tokens"] = (
            report["known_input_tokens"] if report["usage_complete"] else None
        )
        report["output_tokens"] = (
            report["known_output_tokens"] if report["usage_complete"] else None
        )
        if mode == "quick":
            assert report.get("response", {}).get("output_text"), (
                "No final quick answer"
            )
        else:
            assert report["workflow"]["status"] == "completed", "Workflow not completed"
            if mode in {"repair", "strata-repair"}:
                assert (
                    report["workflow"]["loop_statistics"]["plan_repair_succeeded_count"]
                    == 1
                ), "Real model did not exercise the repair-success branch"


async def inspect_run(run_id, report):
    """Read durable messages, workflow statistics and usage without invoking any provider."""
    report["run_id"] = run_id
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1:18000/api/v1/",
        headers={"X-API-Key": local.get_settings().api_key},
    ) as client:
        run = await local.request_json(client, "GET", f"runs/{run_id}")
        report["run"] = run
        report["session_id"] = run["session_id"]
        report["messages"] = await local.request_json(
            client, "GET", f"sessions/{run['session_id']}/messages/latest?limit=100"
        )
        workflow = await client.get(f"runs/{run_id}/agent-workflow")
        if workflow.status_code != 404:
            workflow.raise_for_status()
            summary = workflow.json()
            report["workflow"] = await local.request_json(
                client,
                "GET",
                f"agent-workflows/{summary['workflow_execution_id']}/result",
            )
    report["attempts"] = await inspect_attempts(run_id)
    report["usage_complete"] = bool(report["attempts"]) and all(
        attempt["input_tokens"] is not None and attempt["output_tokens"] is not None
        for attempt in report["attempts"]
    )
    for token_type in ("input_tokens", "output_tokens"):
        report[f"known_{token_type}"] = sum(
            attempt[token_type] or 0 for attempt in report["attempts"]
        )
        report[token_type] = (
            report[f"known_{token_type}"] if report["usage_complete"] else None
        )


async def main(args):
    """Keep a private report on failure and close SQL connections; never retry paid requests."""
    report = {"mode": args.mode, "status": "running"}
    try:
        if args.mode == "seed":
            await seed()
        elif args.mode == "inspect":
            await inspect_run(args.run_id, report)
        else:
            await check(args.mode, report)
        report["status"] = "passed"
    except Exception as exc:
        report.update(status="failed", error_type=type(exc).__name__)
        if isinstance(exc, (AssertionError, RuntimeError)):
            report["error"] = str(exc)
    finally:
        await local.engine.dispose()
        with args.output.open("x") as output:
            json.dump(report, output, ensure_ascii=False, indent=2, default=str)
        print(
            json.dumps(
                {
                    k: v
                    for k, v in report.items()
                    if k
                    not in {
                        "workflow",
                        "response",
                        "attempts",
                        "question",
                        "run",
                        "messages",
                    }
                },
                ensure_ascii=False,
            ),
            flush=True,
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode",
        choices=["seed", "quick", "agent", "repair", "strata-repair", "inspect"],
        required=True,
    )
    parser.add_argument(
        "--run-id", help="Existing local run to inspect without a model call"
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Refusing to overwrite report")
    if args.mode == "inspect" and not args.run_id:
        parser.error("inspect requires --run-id")
    asyncio.run(main(args))
