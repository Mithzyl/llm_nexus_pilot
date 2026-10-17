"""Explicit paid DeepSeek-only completion check, with no deployment or Strata requests.

Run from the repo with apps/api/.venv/bin/python, --cases and --output NEW_PATH.
Requires the existing isolated MySQL on 13306 (read-only endpoint lookup) and
apps/api/.env DeepSeek credential. Uses synthetic prompts, no files or user data.
Prints JSON timing/usage evidence, never headers, credentials or reasoning text.
"""

import asyncio
import argparse
import json
import time
import sys
from pathlib import Path

import check_live_worker_evidence as local
import httpx
from sqlalchemy import select

from nexuspilot_models.contracts import (
    Message,
    ModelRequest,
    ProviderName,
    ReasoningConfiguration,
    ToolDefinition,
)
from nexuspilot_models.providers.deepseek import DeepSeekChatProvider
from nexuspilot_models.transport import HttpTransport
from nexuspilot_models.errors import ModelProviderError

RESULTS = []


async def main(cases):
    """Run five bounded synthetic calls against the explicitly authorized DeepSeek endpoint."""
    async with local.database_session_factory() as db:
        endpoint = await db.scalar(
            select(local.LlmProviderEndpoint).where(
                local.LlmProviderEndpoint.provider == "deepseek",
                local.LlmProviderEndpoint.enabled.is_(True),
            )
        )
        if endpoint is None or endpoint.base_url.rstrip("/") not in {
            "https://api.deepseek.com",
            "https://api.deepseek.com/v1",
        }:
            raise RuntimeError(
                "Expected official DeepSeek endpoint in isolated database"
            )
        base_url = endpoint.base_url
    await local.engine.dispose()
    key = local.get_settings().deepseek_api_key
    if key is None:
        raise RuntimeError("Missing DeepSeek credential")
    async with httpx.AsyncClient() as client:
        provider = DeepSeekChatProvider(
            HttpTransport(client, max_retries=0),
            base_url=base_url,
            api_key=key.get_secret_value(),
        )
        base = dict(
            provider=ProviderName.DEEPSEEK,
            model="deepseek-flash",
            reasoning=ReasoningConfiguration(enabled=False),
            max_output_tokens=1024,
            timeout_seconds=90,
        )
        for case, stream, json_mode in [
            ("text", False, False),
            ("stream_text", True, False),
            ("json_object", False, True),
            ("stream_json_object", True, True),
        ]:
            if case not in cases:
                continue
            request = ModelRequest(
                **base,
                messages=[
                    Message(
                        role="user",
                        content='Return only JSON {"ok":true}'
                        if json_mode
                        else "Reply exactly: completion-ok",
                    )
                ],
                json_object_output=json_mode,
            )
            started = time.monotonic()
            try:
                if stream:
                    events = [e async for e in provider.stream(request)]
                    from nexuspilot_models.contracts import ModelResponse

                    result = ModelResponse.model_validate(events[-1].data["response"])
                else:
                    result = await provider.generate(request)
            except ModelProviderError as error:
                report(
                    case, started, error.response, passed=False, error=error.error_type
                )
                continue
            assert result.finish_reason == "stop" and result.text
            valid = not json_mode or result.structured_output == {"ok": True}
            report(
                case,
                started,
                result,
                passed=valid,
                error=None if valid else "unexpected_json_value",
            )
            if json_mode:
                print(
                    json.dumps(
                        {
                            "case": case,
                            "synthetic_output": result.text,
                            "parsed": result.structured_output,
                        }
                    ),
                    flush=True,
                )
        if "tools" not in cases:
            return
        tool = ToolDefinition(
            name="lookup_test_value",
            description="Return the test value. Must be called before answering.",
            input_schema={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        )
        messages = [
            Message(
                role="user",
                content="Call lookup_test_value to obtain the value, then report that value. Do not guess it.",
            )
        ]
        started = time.monotonic()
        result = await provider.generate(
            ModelRequest(**base, messages=messages, tools=[tool])
        )
        assert len(result.tool_calls) == 1 and result.tool_calls[0].name == tool.name
        report("tool_request", started, result)
        messages += [
            Message(
                role="assistant",
                content=result.text or "",
                tool_calls=result.tool_calls,
            ),
            Message(
                role="tool",
                content='{"value":"completion-tool-42"}',
                tool_call_id=result.tool_calls[0].id,
            ),
        ]
        started = time.monotonic()
        result = await provider.generate(
            ModelRequest(**base, messages=messages, tools=[tool])
        )
        assert result.finish_reason == "stop" and "completion-tool-42" in (
            result.text or ""
        )
        report("tool_continuation_thinking_disabled", started, result)


def report(case, started, response, *, passed=True, error=None):
    """Emit only synthetic-case timing and provider-reported accounting."""
    record = {
        "case": case,
        "status": "passed" if passed else "failed",
        "seconds": round(time.monotonic() - started, 3),
        "input_tokens": response.input_tokens if response else None,
        "output_tokens": response.output_tokens if response else None,
        "cached_tokens": response.cached_tokens if response else None,
        "finish_reason": response.finish_reason if response else None,
        "error": error,
        "synthetic_output": response.text if response else None,
        "estimated_cost": None,
        "cost_note": "Direct protocol check; no price estimate applied, not zero cost.",
    }
    RESULTS.append(record)
    print(
        json.dumps(
            {key: value for key, value in record.items() if key != "synthetic_output"}
        ),
        flush=True,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cases",
        nargs="+",
        choices=["text", "stream_text", "json_object", "stream_json_object", "tools"],
        default=["text", "stream_text", "json_object", "tools"],
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Refusing to overwrite evidence")
    try:
        asyncio.run(main(args.cases))
    finally:
        with args.output.open("x") as output:
            json.dump(RESULTS, output, ensure_ascii=False, indent=2)
    if any(record["status"] != "passed" for record in RESULTS):
        sys.exit(1)
