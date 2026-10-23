"""Run two explicitly authorized paid local Go checks against real MySQL/MinIO.

Run from apps/api with PYTHONPATH=src .venv/bin/python -m
nexuspilot_api.commands.verify_python_agent. Requires the configured Python socket,
local API on 8002, and an authorized nexuspilot-web owner. Saves metadata only to
output/security/python-agent-opencode-go-20261024.json; never logs credentials or text.
Use --verify-saved to recheck saved results and container removal without model calls.
"""

import asyncio
import json
import sys
import time
import uuid
from pathlib import Path

import httpx
from sqlalchemy import text

from nexuspilot_api.core.config import get_settings
from nexuspilot_api.infrastructure.database import engine

REPORT = Path("../../output/security/python-agent-opencode-go-20261024.json")


async def rows(query, run_id):
    """Read metadata for this verification's Run only, without loading private bodies."""
    async with engine.connect() as connection:
        result = await connection.execute(text(query), {"run": run_id})
        return [dict(row) for row in result.mappings()]


async def scenario(client, cancel):
    """Exercise one paid Agent Run; cancel only after observing its actual Python process."""
    prompt = (
        "本地Python执行验收。请发现并加载run_command工具，只调用一次Python。"
        + ("执行import time; time.sleep(25)，不要创建文件，等待工具返回。" if cancel else
           "计算sum([12,23,34])，打印结果69，并将UTF-8文本total\\n69\\n保存到result.csv。"
           "必须用Python实际计算，output_files指定result.csv；工具返回后简短确认结果，不要再执行。")
    )
    response = await client.post("/api/v1/runs", json={
        "user_id": "nexuspilot-web", "user_request": prompt,
    })
    response.raise_for_status()
    run_id = response.json()["run_id"]
    started = time.monotonic()
    record = {"run_id": run_id, "scenario": "cancel" if cancel else "calculate"}
    payload = {
        "workflow_name": "general_agent", "workflow_version": "1.0.0",
        "execution_profile": "general_tools_v1", "review_policy": "never",
        "idempotency_key": str(uuid.uuid4()), "stream": True,
        "role_bindings": {"controller": {
            "provider": "opencode", "model": "deepseek-v4.1-flash",
        }},
    }
    request = asyncio.create_task(client.post(
        f"/api/v1/runs/{run_id}/agent-workflows", json=payload,
    ))
    workflow = None
    stop_at = None
    try:
        async with asyncio.timeout(600):
            while True:
                summary = await client.get(f"/api/v1/runs/{run_id}/agent-workflow")
                if summary.status_code == 200:
                    workflow = summary.json()["workflow_execution_id"]
                    record["workflow_id"] = workflow
                    result = await client.get(f"/api/v1/agent-workflows/{workflow}/result")
                    result.raise_for_status()
                    value = result.json()
                    if value["status"] in {"completed", "failed", "cancelled"}:
                        record.update(status=value["status"], final_text_present=bool(
                            (value.get("final_output") or {}).get("final_text")))
                        break
                if cancel and stop_at is None and workflow:
                    calls = await rows(
                        "select c.tool_call_id from llm_tool_calls c join llm_attempts a "
                        "on a.attempt_id=c.attempt_id where a.run_id=:run "
                        "and c.tool_name='run_command'", run_id,
                    )
                    for call in calls:
                        mapping = Path("/tmp/nexus-python-local/workspaces") / (
                            "call-" + call["tool_call_id"] + ".json")
                        if not mapping.exists():
                            continue
                        # Discover this call's workspace from its executor-owned mapping.
                        data = json.loads(mapping.read_text())
                        workspace = data.get("reference")
                        if not workspace:
                            continue
                        state = json.loads((mapping.parent / workspace / "state.json").read_text())
                        name = state.get("container")
                        if not name:
                            continue
                        process = await asyncio.create_subprocess_exec(
                            "docker", "top", name, "-eo", "pid,args",
                            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                        )
                        stdout, _ = await process.communicate()
                        if b"python -I" not in stdout:
                            continue
                        record["python_observed_running"] = True
                        stop_at = time.monotonic()
                        stopped = await client.post(f"/api/v1/agent-workflows/{workflow}/cancel")
                        stopped.raise_for_status()
                        record["cancel_http_status"] = stopped.status_code
                        record["container_name"] = name
                        break
                await asyncio.sleep(0.3)
        await request
        if stop_at:
            record["cancel_completion_seconds"] = round(time.monotonic() - stop_at, 3)
        if not cancel:
            artifacts = await rows(
                "select artifact_id,filename,size_bytes from llm_artifacts where run_id=:run",
                run_id,
            )
            record["artifacts"] = artifacts
            for artifact in artifacts:
                downloaded = await client.get(
                    f"/api/v1/artifacts/{artifact['artifact_id']}/content",
                )
                artifact["download_status"] = downloaded.status_code
                artifact["expected_bytes_match"] = downloaded.content == b"total\n69\n"
    finally:
        if not request.done():
            if workflow:
                await client.post(f"/api/v1/agent-workflows/{workflow}/cancel")
            await request
        record["elapsed_seconds"] = round(time.monotonic() - started, 3)
        record["attempts"] = await rows(
            "select attempt_id,provider,model,status,input_tokens,output_tokens,cached_tokens,"
            "reasoning_tokens,error_code from llm_attempts where run_id=:run order by started_at",
            run_id,
        )
        record["tool_calls"] = await rows(
            "select c.tool_call_id,c.tool_name,c.status,c.error_code from llm_tool_calls c "
            "join llm_attempts a on a.attempt_id=c.attempt_id where a.run_id=:run "
            "order by c.started_at", run_id,
        )
    return record


async def main():
    """Persist each completed scenario before beginning the next paid request."""
    verify_saved = "--verify-saved" in sys.argv
    report = json.loads(REPORT.read_text()) if verify_saved else {
        "date": "2026-10-24", "database": "MySQL", "storage": "MinIO", "runs": [],
    }
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1:8002", timeout=None,
        headers={"X-API-Key": get_settings().api_key},
    ) as client:
        if verify_saved:
            for record in report["runs"]:
                result = await client.get(
                    f"/api/v1/agent-workflows/{record['workflow_id']}/result",
                )
                result.raise_for_status()
                record["rechecked_status"] = result.json()["status"]
                assert record["rechecked_status"] == record["status"]
                if record["scenario"] == "cancel":
                    process = await asyncio.create_subprocess_exec(
                        "docker", "ps", "-a", "--filter",
                        "name=" + record["container_name"], "--format", "{{.Names}}",
                        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                    )
                    stdout, _ = await process.communicate()
                    record["container_removed"] = process.returncode == 0 and not stdout.strip()
                    assert record["container_removed"]
                    assert record["python_observed_running"]
                else:
                    assert record["status"] == "completed" and record["final_text_present"]
                    assert record["artifacts"] and all(
                        item["expected_bytes_match"] for item in record["artifacts"]
                    )
                record["usage_totals"] = {
                    field: sum(attempt[field] or 0 for attempt in record["attempts"])
                    for field in (
                        "input_tokens", "output_tokens", "cached_tokens", "reasoning_tokens",
                    )
                }
            REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
            print("Saved verification rechecked; no model calls.")
            await engine.dispose()
            return
        for cancel in (False, True):
            result = await scenario(client, cancel)
            report["runs"].append(result)
            REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
            print(json.dumps(result), flush=True)
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
