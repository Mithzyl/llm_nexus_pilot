"""Paid, local DeepSeek JSON single-variable checks; never contact Strata.

Run with apps/api/.venv/bin/python and --output NEW_PATH. Uses the real API
in-process via ASGI, real isolated MySQL/MinIO, and the configured DeepSeek
credential. Captures synthetic wire payloads only, never request headers.
One API call and four adapter calls; no automatic retries or deployment.
"""

import argparse
import asyncio
import copy
import hashlib
import json
import time
import uuid
from pathlib import Path

import check_live_worker_evidence as local
import httpx

from nexuspilot_api.main import app
from nexuspilot_models.contracts import (
    ModelRequest,
    ProviderName,
    ReasoningConfiguration,
)
from nexuspilot_models.errors import ModelProviderError


def fingerprint(payload):
    """Hash canonical synthetic wire content so equality excludes headers and timing."""
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def request_from_wire(wire):
    """Reconstruct this text-only fixture without changing the actual provider payload."""
    messages = wire["messages"]
    system = None
    if messages[0]["role"] == "system":
        system = messages[0]["content"]
        messages = messages[1:]
    return ModelRequest(
        provider=ProviderName.DEEPSEEK,
        model=wire["model"],
        messages=messages,
        system_instruction=system,
        reasoning=ReasoningConfiguration(enabled=False),
        json_object_output=True,
        max_output_tokens=wire["max_tokens"],
        timeout_seconds=90,
    )


async def check(report, baseline=None, verify_fix=False):
    """Compare API/adapter identity, then vary model alone and messages alone."""
    async with app.router.lifespan_context(app):
        provider = app.state.provider_registry.resolve(
            ProviderName.DEEPSEEK, "deepseek-v4-flash"
        )
        if provider.base_url.rstrip("/") not in {
            "https://api.deepseek.com",
            "https://api.deepseek.com/v1",
        }:
            raise RuntimeError("Expected official DeepSeek URL")
        current = {}

        async def capture_request(request):
            """Reject other endpoints or unexpected wire mutations before paid dispatch."""
            if str(request.url) != provider.base_url + "/chat/completions":
                raise RuntimeError("Only DeepSeek chat completions are allowed")
            payload = json.loads(request.content)
            expected = current.get("expected_wire")
            if expected is not None and payload != expected:
                raise RuntimeError("Wire payload differs from controlled input")
            current["wire"] = payload
            current["wire_sha256"] = fingerprint(payload)

        async def capture_response(response):
            """Read the JSON response once and keep raw synthetic evidence plus usage."""
            await response.aread()
            current["http_status"] = response.status_code
            raw = response.json()
            current["raw_response"] = raw
            current["usage"] = raw.get("usage")

        provider.transport.client.event_hooks["request"].append(capture_request)
        provider.transport.client.event_hooks["response"].append(capture_response)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://127.0.0.1/api/v1/",
            headers={"X-API-Key": local.get_settings().api_key},
            timeout=120,
        ) as api:
            owner = (await local.request_json(api, "GET", "auth/me"))["user_id"]
            session = await local.request_json(
                api,
                "POST",
                "sessions",
                json={"user_id": owner, "title": "JSON wire parity"},
            )
            prompt = 'Return only JSON {"ok":true}'
            turn = await local.request_json(
                api,
                "POST",
                f"sessions/{session['session_id']}/turns",
                json={
                    "user_id": owner,
                    "content_text": prompt,
                    "idempotency_key": str(uuid.uuid4()),
                },
            )
            current = {"case": "api_anchored_v4"}
            report["calls"].append(current)
            started = time.monotonic()
            if baseline:
                current.update(
                    copy.deepcopy(json.loads(baseline.read_text())["calls"][0])
                )
                current["reused_evidence"] = str(baseline)
                response = None
            else:
                response = await api.post(
                    "responses",
                    json={
                        "run_id": turn["run"]["run_id"],
                        "current_user_message_id": turn["message"]["message_id"],
                        "provider": "deepseek",
                        "model": "deepseek-v4-flash",
                        "input": prompt,
                        "reasoning": {"enabled": False},
                        "json_object_output": True,
                        "max_output_tokens": 1024,
                        "timeout_seconds": 90,
                        "idempotency_key": str(uuid.uuid4()),
                    },
                )
            if response is not None:
                current.update(
                    seconds=round(time.monotonic() - started, 3),
                    run_id=turn["run"]["run_id"],
                    api_status=response.status_code,
                    api_result=response.json(),
                )
            wire = copy.deepcopy(current["wire"])
            classify(current)
            variants = [
                ("adapter_same_wire_v4", wire),
                ("adapter_only_model_flash", {**wire, "model": "deepseek-flash"}),
                (
                    "adapter_only_messages_minimal_v4",
                    {**wire, "messages": [{"role": "user", "content": prompt}]},
                ),
                (
                    "adapter_minimal_flash",
                    {
                        **wire,
                        "model": "deepseek-flash",
                        "messages": [{"role": "user", "content": prompt}],
                    },
                ),
            ]
            for label, expected in variants:
                current = {"case": label, "expected_wire": expected}
                report["calls"].append(current)
                started = time.monotonic()
                request = request_from_wire(expected)
                actual = provider._build_payload(request, stream=False)
                if actual != expected:
                    assert verify_fix
                    assert actual["messages"][0]["role"] == "system"
                    assert "valid JSON object" in actual["messages"][0]["content"]
                    assert actual["messages"][1:] == expected["messages"]
                    assert {k: v for k, v in actual.items() if k != "messages"} == {
                        k: v for k, v in expected.items() if k != "messages"
                    }
                    current["repair_delta"] = (
                        "Added JSON system guidance; all other fields unchanged"
                    )
                    current["expected_wire"] = actual
                try:
                    await provider.generate(request)
                except ModelProviderError as error:
                    current["adapter_error"] = error.error_type
                current["seconds"] = round(time.monotonic() - started, 3)
                current.pop("expected_wire")
                classify(current)
            report["api_adapter_payload_equal"] = (
                report["calls"][0]["wire"] == report["calls"][1]["wire"]
            )


def classify(record):
    """Validate raw provider content independently from either application execution path."""
    raw = record.get("raw_response") or {}
    choice = (raw.get("choices") or [{}])[0]
    text = (choice.get("message") or {}).get("content")
    record["finish_reason"] = choice.get("finish_reason")
    try:
        parsed = json.loads(text)
        record["json_valid"] = True
        record["expected_value"] = parsed == {"ok": True}
    except (ValueError, TypeError):
        record["json_valid"] = False
        record["expected_value"] = False
    print(
        json.dumps(
            {
                key: value
                for key, value in record.items()
                if key not in {"wire", "raw_response", "api_result"}
            },
            ensure_ascii=False,
        ),
        flush=True,
    )


async def main(output, baseline=None, verify_fix=False):
    """Preserve completed paid-call evidence on failure and close only local test resources."""
    report = {"calls": [], "status": "running"}
    try:
        await check(report, baseline, verify_fix)
        report["status"] = "completed"
    except Exception as error:
        report.update(status="failed", error_type=type(error).__name__)
    finally:
        await local.engine.dispose()
        with output.open("x") as file:
            json.dump(report, file, ensure_ascii=False, indent=2, default=str)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--verify-fix", action="store_true")
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Refusing to overwrite evidence")
    asyncio.run(main(args.output, args.baseline, args.verify_fix))
