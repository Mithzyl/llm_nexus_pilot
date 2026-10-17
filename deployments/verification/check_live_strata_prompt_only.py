"""Paid, local prompt-only workflow evaluation; never deploy or change business modules.

Run with apps/api/.venv/bin/python deployments/verification/check_live_strata_prompt_only.py
--output NEW_PATH. Requires apps/api/.env and the existing isolated MySQL 13306 /
MinIO 19000 verification services. Runs one natural workflow and one controlled
missing-field repair workflow, at most 20 actual Strata calls in total, no HTTP
retries. ASGI executes the real API, node validators and repair code in-process.
Only the wire response_format is removed; full prompts and local JSON validation
remain. Controlled faults alter a returned validation input, not saved provider
responses; they are explicitly recorded and excluded from natural failure rates.
Reports contain synthetic answers and IDs, never credentials or hidden reasoning.
"""

import argparse
import asyncio
import hashlib
import json
import time
import uuid

import httpx
import check_live_worker_evidence as local
from check_live_strata_plan_repair import inspect_attempts
from nexuspilot_api.main import app
from nexuspilot_api.features.agent_runtime.services.agent_model_node_service import (
    AgentModelNodeService,
)
from nexuspilot_models.providers.strata import StrataChatProvider
from pydantic import ValidationError


async def evaluate(report):
    """Exercise real local workflows with scoped wire overrides and separate fault evidence."""
    original_payload = StrataChatProvider._build_payload
    original_generate = StrataChatProvider.generate
    original_round = AgentModelNodeService._call_model_round
    state = {"scenario": None, "calls": 0, "injected": set()}

    def payload_without_format(self, request, *, stream):
        """Remove only the supplier format flag; retain the internal validation request."""
        payload = original_payload(self, request, stream=stream)
        before = dict(payload)
        removed = payload.pop("response_format", None)
        assert {k: v for k, v in before.items() if k != "response_format"} == payload
        report["wire_checks"].append({
            "scenario": state["scenario"], "removed": removed,
            "node": request.metadata.get("agent_node_key"),
            "messages_sha256": hashlib.sha256(
                json.dumps(payload["messages"], sort_keys=True).encode()
            ).hexdigest(),
        })
        return payload

    async def measured_generate(self, request):
        """Measure each real supplier attempt and stop before exceeding the experiment limit."""
        assert request.provider.value == "strata"
        assert self.base_url.rstrip("/") == "https://mithfusion.site/v1"
        if state["calls"] >= 20:
            raise RuntimeError("Experiment stopped at 20 actual calls")
        state["calls"] += 1
        row = {"scenario": state["scenario"], "node": request.metadata.get("agent_node_key"),
               "output_repair": request.metadata.get("node_output_repair") == "1"}
        report["calls"].append(row)
        started = time.monotonic()
        print(json.dumps({"call": state["calls"], **row}), flush=True)
        try:
            result = await original_generate(self, request)
            row.update(status="completed", text=result.text, input_tokens=result.input_tokens,
                       output_tokens=result.output_tokens, finish_reason=result.finish_reason)
            return result
        except Exception as error:
            row.update(status="failed", error_type=getattr(error, "error_type", type(error).__name__))
            raise
        finally:
            row["elapsed_seconds"] = round(time.monotonic() - started, 3)

    async def measured_round(self, workflow, node, **kwargs):
        """Check natural schema validity, then inject one explicit missing field per target."""
        result = await original_round(self, workflow, node, **kwargs)
        key = node.node_key.split(".")[0]
        schema = kwargs["model_output"]
        raw = result.structured_output
        if raw is None:
            try:
                raw = json.loads(result.output_text or "")
            except ValueError:
                raw = result.output_text
        row = {"scenario": state["scenario"], "node": node.node_key,
               "attempt_id": result.id, "repair": kwargs.get("output_repair", False)}
        try:
            schema.model_validate(raw)
            row["natural_schema_pass"] = True
        except ValidationError as error:
            row.update(natural_schema_pass=False, errors=[
                {"loc": list(e["loc"]), "type": e["type"]} for e in error.errors()
            ])
        report["validation_inputs"].append(row)
        fields = {"controller_planning": "decision_summary", "worker_execution": "summary",
                  "independent_review": "review_summary"}
        if (state["scenario"] == "controlled_repair" and key in fields
                and key not in state["injected"] and not row["repair"]
                and row["natural_schema_pass"]):
            modified = dict(raw)
            modified.pop(fields[key])
            state["injected"].add(key)
            row["injected_missing_field"] = fields[key]
            return result.model_copy(update={"structured_output": modified,
                                              "output_text": json.dumps(modified, ensure_ascii=False)})
        return result

    StrataChatProvider._build_payload = payload_without_format
    StrataChatProvider.generate = measured_generate
    AgentModelNodeService._call_model_round = measured_round
    try:
        async with app.router.lifespan_context(app), httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1/api/v1/",
            headers={"X-API-Key": local.get_settings().api_key}, timeout=660,
        ) as client:
            owner = (await local.request_json(client, "GET", "auth/me"))["user_id"]
            for scenario in ("natural", "controlled_repair"):
                state["scenario"] = scenario
                started = time.monotonic()
                entry = {"scenario": scenario}
                report["workflows"].append(entry)
                question = (
                    "请用三句话解释 Python 列表与元组的区别，说明可变性、语法和用途。"
                    "只安排一个 planner 分析任务，不使用工具。"
                    "为允许节点格式校验失败后修正，请给该任务 max_model_calls=2。"
                )
                session = await local.request_json(client, "POST", "sessions", json={
                    "user_id": owner, "title": "Strata prompt-only " + scenario})
                turn = await local.request_json(client, "POST", f"sessions/{session['session_id']}/turns",
                    json={"user_id": owner, "content_text": question, "idempotency_key": str(uuid.uuid4())})
                run_id = entry["run_id"] = turn["run"]["run_id"]
                binding = {"provider": "strata", "model": "tiel-coder-35b-a3b-ud-q4_k_xl",
                           "structured_output_mode": "prompted_json", "timeout_seconds": 240}
                entry["result"] = await local.request_json(client, "POST", f"runs/{run_id}/agent-workflows",
                    json={"workflow_version": "1.1.0", "idempotency_key": str(uuid.uuid4()),
                          "max_workflow_rounds": 1, "max_model_calls": 10,
                          "role_bindings": {role: binding for role in ("controller", "planner", "reviewer")}})
                entry["elapsed_seconds"] = round(time.monotonic() - started, 3)
                entry["attempts"] = await inspect_attempts(run_id)
                print(json.dumps({"scenario": scenario, "status": entry["result"]["status"],
                                  "elapsed_seconds": entry["elapsed_seconds"]}), flush=True)
    finally:
        StrataChatProvider._build_payload = original_payload
        StrataChatProvider.generate = original_generate
        AgentModelNodeService._call_model_round = original_round


async def main(path):
    """Persist partial diagnostic results on failure without sending replacement requests."""
    report = {"calls": [], "wire_checks": [], "validation_inputs": [], "workflows": []}
    try:
        await evaluate(report)
    finally:
        await local.engine.dispose()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    from pathlib import Path

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Refusing to overwrite evidence")
    asyncio.run(main(args.output))
