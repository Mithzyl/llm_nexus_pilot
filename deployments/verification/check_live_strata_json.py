"""Two paid Strata JSON calls after DeepSeek repair acceptance, without deployment.

Run with apps/api/.venv/bin/python --output NEW_PATH. Reads the local verification
DB endpoint and apps/api/.env key. Saves synthetic wire/SSE evidence and usage,
never headers or keys. Exercises incremental stream and collected Agent transport,
not a whole workflow. No retries; at most 1024 generated tokens per call.
"""

import argparse
import asyncio
import json
import time
from pathlib import Path

import check_live_worker_evidence as local
import httpx
from sqlalchemy import select

from nexuspilot_models.contracts import ModelRequest, ModelResponse, ProviderName
from nexuspilot_models.errors import ModelProviderError
from nexuspilot_models.providers.strata import StrataChatProvider
from nexuspilot_models.transport import HttpTransport


class RecordingStream(httpx.AsyncByteStream):
    """Forward bytes immediately while retaining bounded synthetic protocol evidence."""

    def __init__(self, inner, record, started):
        """Bind one response stream to its current diagnostic record."""
        self.inner, self.record, self.started = inner, record, started
        self.parts = []
        self.size = 0

    async def __aiter__(self):
        """Do not buffer delivery; fail the diagnostic if raw evidence exceeds 2 MiB."""
        async for chunk in self.inner:
            self.record.setdefault(
                "first_byte_seconds", round(time.monotonic() - self.started, 3)
            )
            self.size += len(chunk)
            if self.size > 2 * 1024 * 1024:
                raise RuntimeError("Synthetic evidence exceeds 2 MiB")
            self.parts.append(chunk)
            yield chunk

    async def aclose(self):
        """Keep received evidence on success or failure and release the upstream connection."""
        self.record["raw_sse"] = b"".join(self.parts).decode("utf-8", errors="replace")
        await self.inner.aclose()


async def check(report):
    """Use the configured Strata origin only and run two short JSON requests sequentially."""
    async with local.database_session_factory() as db:
        endpoint = await db.scalar(
            select(local.LlmProviderEndpoint).where(
                local.LlmProviderEndpoint.provider == "strata",
                local.LlmProviderEndpoint.enabled.is_(True),
            )
        )
        if (
            endpoint is None
            or endpoint.base_url.rstrip("/") != "https://mithfusion.site/v1"
        ):
            raise RuntimeError("Unexpected Strata endpoint")
        base_url = endpoint.base_url.rstrip("/")
    key = local.get_settings().strata_api_key
    if key is None:
        raise RuntimeError("Missing configured Strata key")
    async with httpx.AsyncClient() as client:
        provider = StrataChatProvider(
            HttpTransport(client, max_retries=0),
            base_url=base_url,
            api_key=key.get_secret_value(),
        )
        for mode in ("stream", "generate"):
            record = {"mode": mode}
            report["calls"].append(record)
            started = time.monotonic()

            async def capture_request(request):
                """Reject other origins and save only synthetic JSON, never authentication."""
                if str(request.url) != base_url + "/chat/completions":
                    raise RuntimeError("Unexpected request target")
                record["wire"] = json.loads(request.content)

            async def capture_response(response):
                """Capture raw bytes without delaying streaming until the response completes."""
                record["http_status"] = response.status_code
                response.stream = RecordingStream(response.stream, record, started)

            client.event_hooks["request"] = [capture_request]
            client.event_hooks["response"] = [capture_response]
            request = ModelRequest(
                provider=ProviderName.STRATA,
                model="tiel-coder-35b-a3b-ud-q4_k_xl",
                messages=[{"role": "user", "content": 'Return only JSON {"ok":true}'}],
                json_object_output=True,
                max_output_tokens=1024,
                timeout_seconds=180,
            )
            try:
                if mode == "stream":
                    events = [event async for event in provider.stream(request)]
                    result = ModelResponse.model_validate(events[-1].data["response"])
                else:
                    result = await provider.generate(request)
                record.update(
                    passed=result.structured_output == {"ok": True},
                    text=result.text,
                    input_tokens=result.input_tokens,
                    output_tokens=result.output_tokens,
                    cached_tokens=result.cached_tokens,
                    finish_reason=result.finish_reason,
                )
            except ModelProviderError as error:
                record.update(passed=False, error=error.error_type)
            record["seconds"] = round(time.monotonic() - started, 3)
            for line in record.get("raw_sse", "").splitlines():
                if not line.startswith("data: ") or line == "data: [DONE]":
                    continue
                try:
                    chunk = json.loads(line[6:])
                except ValueError:
                    continue
                if chunk.get("usage"):
                    record["raw_usage"] = chunk["usage"]
            print(
                json.dumps(
                    {k: v for k, v in record.items() if k not in {"wire", "raw_sse"}},
                    ensure_ascii=False,
                ),
                flush=True,
            )


async def main(output):
    """Persist fresh reports even on failure; failed checks never silently retry the model."""
    report = {"provider": "strata", "calls": []}
    try:
        await check(report)
    except Exception as error:
        report["error_type"] = type(error).__name__
    finally:
        await local.engine.dispose()
        with output.open("x") as file:
            json.dump(report, file, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Refusing to overwrite evidence")
    asyncio.run(main(args.output))
