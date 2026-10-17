"""Replay one saved synthetic Strata attempt locally, without retrying or changing its messages.

Run with apps/api/.venv/bin/python --attempt-id ID --output NEW_PATH. Requires
the existing isolated MySQL/MinIO verification environment and apps/api/.env.
Only the two synthetic 2026-10-17 failures are allowed. This incurs one real
Strata request; it does not create an API Run or deploy. Headers, keys, model
reasoning and message bodies are not written to the diagnostic report.
"""

import argparse
import asyncio
import hashlib
import json
import time
from pathlib import Path

import httpx
from sqlalchemy import select

import check_live_worker_evidence as local
from check_live_strata_json import RecordingStream
from nexuspilot_api.models import LlmModelAttempt, LlmContextSource
from nexuspilot_models.contracts import ModelRequest
from nexuspilot_models.errors import ModelProviderError
from nexuspilot_models.providers.strata import StrataChatProvider
from nexuspilot_models.transport import HttpTransport

ALLOWED_ATTEMPTS = {
    "3450e918-5f42-44ef-ba18-b6a96ce24a57",
    "c4cd8c91-c2b1-40ce-a1e4-d549e0bb0269",
}


async def check(attempt_id: str, report: dict, *, audit_only: bool = False) -> None:
    """Reconstruct the recorded request and retain only bounded, key-redacted error text."""
    async with local.database_session_factory() as db:
        attempt = await db.get(LlmModelAttempt, attempt_id)
        assert attempt and attempt.provider == "strata"
        obj = await local.ObjectStorage().open_object(attempt.raw_request_uri)
        raw = json.loads(b"".join(obj.chunks))
        sources = list(await db.scalars(select(LlmContextSource).where(
            LlmContextSource.context_build_id == attempt.context_build_id,
            LlmContextSource.message_role == "system",
        ).order_by(LlmContextSource.source_order)))
        selected = [s for s in sources if s.selection_status.value == "selected"]
        assert selected, "Missing persisted system instructions"
        for source in selected:
            assert source.content_text is not None
            assert hashlib.sha256(source.content_text.encode()).hexdigest() == source.content_hash
        system_instruction = "\n\n".join(s.content_text for s in selected)
        report["system_source_count"] = len(selected)
        report["assembled_system_matches_raw_instructions"] = system_instruction == raw["instructions"]
        endpoint = await db.scalar(select(local.LlmProviderEndpoint).where(
            local.LlmProviderEndpoint.provider == "strata",
            local.LlmProviderEndpoint.enabled.is_(True),
        ))
        assert endpoint.base_url.rstrip("/") == "https://mithfusion.site/v1"
    request = ModelRequest(
        **{key: raw[key] for key in (
            "provider", "model", "tools", "output_schema", "json_object_output",
            "reasoning", "temperature", "max_output_tokens", "timeout_seconds", "metadata",
        )},
        messages=raw["effective_messages"], system_instruction=system_instruction,
    )
    secret = local.get_settings().strata_api_key.get_secret_value()
    capture = {}
    started = time.monotonic()

    async def record_response(response):
        """Collect synthetic stream evidence in memory, never authentication headers."""
        capture["http_status"] = response.status_code
        response.stream = RecordingStream(response.stream, capture, started)

    async with httpx.AsyncClient(event_hooks={"response": [record_response]}) as client:
        provider = StrataChatProvider(HttpTransport(client, max_retries=0),
                                      base_url=endpoint.base_url, api_key=secret)
        wire = provider._build_payload(request, stream=True)
        report["wire_sha256"] = hashlib.sha256(json.dumps(wire, sort_keys=True).encode()).hexdigest()
        report["message_count"] = len(wire["messages"])
        if audit_only:
            report["status"] = "audit_only_no_provider_request"
            return
        try:
            result = await provider.generate(request)
            report.update(status="completed", input_tokens=result.input_tokens,
                          output_tokens=result.output_tokens, finish_reason=result.finish_reason)
        except ModelProviderError as error:
            report.update(status="failed", error_type=error.error_type, diagnostic=error.raw_error)
        finally:
            report["elapsed_seconds"] = round(time.monotonic() - started, 3)
            report["http_status"] = capture.get("http_status")
            report["error_frames"] = []
            for line in capture.get("raw_sse", "").splitlines():
                if not line.startswith("data: ") or line == "data: [DONE]":
                    continue
                try:
                    frame = json.loads(line[6:])
                except ValueError:
                    continue
                if frame.get("error") is not None:
                    text = json.dumps(frame["error"], ensure_ascii=False)
                    report["error_frames"].append(text.replace(secret, "[REDACTED]")[:1000])


async def main(args) -> None:
    """Save one fresh local diagnostic even if the provider fails; never retry implicitly."""
    report = {"attempt_id": args.attempt_id, "provider": "strata"}
    try:
        await check(args.attempt_id, report, audit_only=args.audit_only)
    finally:
        await local.engine.dispose()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--attempt-id", choices=sorted(ALLOWED_ATTEMPTS), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--audit-only", action="store_true",
                        help="Verify persisted messages and hashes without a provider request")
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Refusing to overwrite evidence")
    asyncio.run(main(args))
