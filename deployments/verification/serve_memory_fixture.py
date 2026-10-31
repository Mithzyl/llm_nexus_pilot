"""Local memory browser verification, real DB/storage and network-free model adapters.

Run from repo root with PYTHONPATH=apps/api/src:packages/models/src and API Python.
--configure-key provisions a private key only if absent in apps/api/.env.
--role api serves on 8000; --role worker runs the existing independent consumer.
Both roles use the same private .env. No Provider HTTP calls are made. Stop these
fixture processes explicitly before returning to normal model-enabled operation.
"""

import argparse
import asyncio
import base64
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import re

from dotenv import dotenv_values, set_key
from nexuspilot_models.contracts import (
    ModelResponse,
    ProviderName,
    StreamEvent,
    StreamEventType,
    ToolCall,
)
from nexuspilot_models.registry import ProviderRegistry
from sqlalchemy import select
import uvicorn

from nexuspilot_api.core.config import API_ENV_FILE, get_settings
from nexuspilot_api.infrastructure.database import database_session_factory
from nexuspilot_api.main import app, lifespan
from nexuspilot_api.models import LlmRegisteredModel


class MemoryFixtureProvider:
    """Produce deterministic memory tool calls without any HTTP client or Provider fallback."""

    async def generate(self, request):
        """Exercise real memory operations; only return text obtained from their results."""
        if request.metadata.get("agent_role") == "reviewer":
            # This fixture checks orchestration, not factual correctness of model text.
            units = None
            for message in request.messages:
                try:
                    value = json.loads(message.content or "")
                except (ValueError, TypeError):
                    continue
                if isinstance(value, dict):
                    context = value.get("candidate_context", value)
                    if isinstance(context, dict) and "review_units" in context:
                        units = context["review_units"]
                        break
            if units is None:
                raise ValueError("Fixed reviewer requires the actual review unit batch")
            text = json.dumps(
                {
                    "decisions": [
                        {
                            "unit_id": unit["unit_id"],
                            "verdict": "supported",
                            "feedback": "Fixed orchestration sample",
                            "source_aliases": unit["source_aliases"],
                        }
                        for unit in units
                    ]
                }
            )
            return ModelResponse(
                text=text,
                finish_reason="stop",
                input_tokens=0,
                output_tokens=0,
                latency_ms=1,
                raw_response={"fixture": True},
            )
        if os.environ.get("NEXUS_FIXTURE_PYTHON_CONTINUATION") == "1":
            from python_continuation_fixture import generate_python_step
            return await generate_python_step(request)
        tools = [m for m in request.messages if m.role.value == "tool"]
        if tools:
            value = json.loads(tools[-1].content)
            result = value.get("result", {})
            text = "MEMORY_CHECK " + json.dumps(result, ensure_ascii=False)
            return ModelResponse(
                text=text,
                finish_reason="stop",
                input_tokens=0,
                output_tokens=0,
                latency_ms=1,
                raw_response={"fixture": True},
            )
        user = next(
            (m.content for m in reversed(request.messages) if m.role.value == "user"),
            "",
        )
        try:
            obj = json.loads(user)
            user = obj.get("objective", user) if isinstance(obj, dict) else user
        except ValueError:
            pass
        project = "PROJECT" in user
        if "ASK_INPUT" in user or "ASK_APPROVAL" in user:
            return ModelResponse(
                tool_calls=[
                    ToolCall(
                        id="fixture-human-question",
                        name="request_user_input",
                        arguments={
                            "kind": "approval" if "ASK_APPROVAL" in user else "input",
                            "question": "请确认是否继续。"
                            if "ASK_APPROVAL" in user
                            else "请填写本次任务的目标城市。",
                        },
                    )
                ],
                finish_reason="tool_calls",
                input_tokens=0,
                output_tokens=0,
                latency_ms=1,
                raw_response={"fixture": True},
            )
        match = re.search(r"记住(MEMORY_BROWSER_[A-Z_]+)", user)
        arguments = {"scope": "project" if project else "personal"}
        if match:
            name = "save_memory"
            arguments["text"] = match[1]
        else:
            name = "query_memory"
        return ModelResponse(
            tool_calls=[
                ToolCall(id="memory-browser-call", name=name, arguments=arguments)
            ],
            finish_reason="tool_calls",
            input_tokens=0,
            output_tokens=0,
            latency_ms=1,
            raw_response={"fixture": True},
        )

    async def stream(self, request):
        """Use the same fixed response for quick replies, with time to observe browser state."""
        yield StreamEvent(type=StreamEventType.STARTED, sequence=1)
        await asyncio.sleep(float(os.environ.get("NEXUS_FIXTURE_DELAY_SECONDS", "1")))
        response = await self.generate(request)
        if response.text:
            yield StreamEvent(
                type=StreamEventType.TEXT_DELTA,
                sequence=2,
                data={"delta": response.text},
            )
        yield StreamEvent(
            type=StreamEventType.COMPLETED,
            sequence=3,
            data={"response": response.model_dump(mode="json")},
        )


@asynccontextmanager
async def fixture_lifespan(application):
    """Replace every registered model adapter, never letting test requests reach paid Providers."""
    crash_marker = os.environ.get("NEXUS_FIXTURE_CRASH_AFTER_TOOL")
    if crash_marker:
        from nexuspilot_api.services.agent_file_tool_service import AgentFileToolService

        original_execute = AgentFileToolService.execute

        async def crash_once_after_saved_tool(self, **kwargs):
            """Exit this fixture process once after a real tool-result transaction commits."""
            result = await original_execute(self, **kwargs)
            try:
                with open(crash_marker, "x", encoding="utf-8") as marker:
                    marker.write(result.tool_call_id)
            except FileExistsError:
                return result
            os._exit(86)

        AgentFileToolService.execute = crash_once_after_saved_tool
    async with lifespan(application):
        registry = ProviderRegistry()
        async with database_session_factory() as db:
            rows = list(await db.scalars(select(LlmRegisteredModel)))
        for provider in ProviderName:
            models = frozenset(
                r.model_identifier for r in rows if r.provider == provider.value
            )
            if models:
                registry.register(
                    provider, MemoryFixtureProvider(), allowed_models=models
                )
        application.state.provider_registry = registry
        print(
            "MEMORY FIXTURE: no external model requests; private key loaded="
            + str(bool(get_settings().memory_encryption_key)),
            flush=True,
        )
        yield


def configure_key():
    """Provision a secret without printing it, rotating it, or copying any other configuration."""
    path = Path(API_ENV_FILE)
    if path.is_symlink() or not path.is_file():
        raise RuntimeError("Expected existing regular API .env")
    existing = dotenv_values(path).get("NEXUSPILOT_MEMORY_ENCRYPTION_KEY")
    if existing:
        if len(base64.b64decode(existing, altchars=b"-_", validate=True)) != 32:
            raise RuntimeError("Existing key is invalid; refusing rotation")
    else:
        value = base64.urlsafe_b64encode(os.urandom(32)).decode()
        set_key(path, "NEXUSPILOT_MEMORY_ENCRYPTION_KEY", value)
    path.chmod(0o600)
    print("Private memory key configured; value not displayed")


def main():
    """Start one explicit local role or provision the shared private memory setting."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--configure-key", action="store_true")
    parser.add_argument("--role", choices=["api", "worker"], default="api")
    args = parser.parse_args()
    if args.configure_key:
        configure_key()
        return
    get_settings().workflow_execution_backend = "worker"
    if args.role == "worker":
        from nexuspilot_api.commands import workflow_worker

        workflow_worker.lifespan = fixture_lifespan
        asyncio.run(workflow_worker.main())
    else:
        app.router.lifespan_context = fixture_lifespan
        uvicorn.run(app, host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
