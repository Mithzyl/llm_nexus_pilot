"""Explicit local-only seed for testing real reviewer/refinement execution.

This module is never imported by the API. A verification runner must register the
distinct SEED_MODEL and its catalog, install SeedProvider in its own registry, and
enter seed_first_turn for one newly-created test Run. The seed has no HTTP client.
Later model rounds retain the runner's real provider bindings. Seed attempts are
local fixture records, not evidence of a Strata request or model quality.
"""

import json
from contextlib import contextmanager
from uuid import UUID

from nexuspilot_models.contracts import ModelResponse, ProviderName, ToolCall

SEED_MODEL = "verification-fixed-candidate"


class SeedProvider:
    """Use the local OpenAI-shaped test slot, with an unmistakable non-provider model name."""

    name = ProviderName.OPENAI

    def __init__(self, file_id):
        """Bind one test attachment; the actual file tool still enforces ownership and parsing."""
        self.file_id = str(UUID(file_id))

    async def generate(self, request):
        """Read genuine source blocks, then return a fixed wrong credit count with honest provenance."""
        if (
            request.model != SEED_MODEL
            or request.provider != self.name
            or request.metadata.get("agent_role") != "controller"
        ):
            raise ValueError(
                "Seed provider only accepts the explicit controller fixture model"
            )
        previous = [m for m in request.messages if m.role.value == "tool"]
        common = dict(
            latency_ms=0,
            input_tokens=0,
            output_tokens=0,
            raw_response={
                "record_kind": "verification_seed",
                "provider_request_sent": False,
                "fixture_model": SEED_MODEL,
            },
        )
        if not previous:
            return ModelResponse(
                tool_calls=[
                    ToolCall(
                        id="seed-read",
                        name="read_file_content",
                        arguments={"file_id": self.file_id, "offset": 0, "limit": 20},
                    )
                ],
                finish_reason="tool_calls",
                **common,
            )
        result = json.loads(previous[-1].content)
        source = result.get("result") or {}
        blocks = source.get("data", {}).get("blocks", [])
        if (
            previous[-1].tool_name != "read_file_content"
            or result.get("status") != "completed"
            or source.get("file_id") != self.file_id
            or not any("Degree project 30 credits" in b.get("text", "") for b in blocks)
        ):
            raise ValueError(
                "Seed requires the expected real source text; no fabricated evidence"
            )
        alias = source.get("source_alias", "")
        if not (
            isinstance(alias, str) and alias.startswith("S") and alias[1:].isdigit()
        ):
            raise ValueError("Seed requires a platform-issued source alias")
        return ModelResponse(
            text=f"该论文的 Degree project 学分是 300 [{alias}]。",
            finish_reason="stop",
            **common,
        )


@contextmanager
def seed_first_turn(orchestrator_class, run_id, binding):
    """Temporarily seed only a named Run's first controller turn; restore the method on exit."""
    target = str(UUID(run_id))
    if binding.provider != ProviderName.OPENAI or binding.model != SEED_MODEL:
        raise ValueError(
            "Seed binding must identify the local fixture, never a real provider model"
        )
    original = orchestrator_class._turn
    if getattr(original, "verification_seed", False):
        raise RuntimeError("Nested seed installation is not allowed")

    async def scoped(self, workflow, node, payload, task, state, purpose, values):
        """Preserve real bindings for other Runs, reviewers and every correction round."""
        if (
            workflow.run_id == target
            and task.assigned_role == "controller"
            and values.get("workflow_round") == 1
        ):
            payload = payload.model_copy(
                update={
                    "role_bindings": {**payload.role_bindings, "controller": binding},
                    "reasoning": None,
                }
            )
        return await original(
            self, workflow, node, payload, task, state, purpose, values
        )

    scoped.verification_seed = True
    orchestrator_class._turn = scoped
    try:
        yield
    finally:
        if orchestrator_class._turn is not scoped:
            raise RuntimeError(
                "Verification hook changed unexpectedly; refuse to overwrite it"
            )
        orchestrator_class._turn = original
