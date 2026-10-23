"""Explicit local-only seed for testing real reviewer/refinement execution.

This module is never imported by the API. A verification runner must register the
distinct SEED_MODEL and its catalog, install SeedProvider in its own registry, and
enter seed_first_turn for one newly-created test Run. The seed has no HTTP client.
Later model rounds retain the runner's real provider bindings. Seed attempts are
local fixture records, not evidence of a Strata request or model quality.
"""

import json
from contextlib import asynccontextmanager, contextmanager
from uuid import UUID

from nexuspilot_models.contracts import ModelResponse, ProviderName, ToolCall

SEED_MODEL = "verification-fixed-candidate"


@asynccontextmanager
async def register_seed(session_factory):
    """Temporarily enable an explicitly owned fixture model; preserve disabled audit rows on exit.

    Refuse an existing enabled row (including a crashed prior run) rather than stealing
    it. A process kill can prevent cleanup; inspect the test Run before manual recovery.
    """
    from nexuspilot_api.models import LlmModelCatalogVersion, LlmRegisteredModel
    from nexuspilot_api.models.base import utc_now
    from nexuspilot_api.models.enums import ModelRegistrationStatus
    from sqlalchemy import select

    async with session_factory() as db:
        row = await db.scalar(
            select(LlmRegisteredModel)
            .where(
                LlmRegisteredModel.provider == ProviderName.OPENAI,
                LlmRegisteredModel.model_identifier == SEED_MODEL,
            )
            .with_for_update()
        )
        if row is not None:
            if not (row.metadata_json or {}).get("verification_seed"):
                raise RuntimeError("Existing model is not owned by this verification")
            if row.status == ModelRegistrationStatus.ENABLED:
                raise RuntimeError(
                    "Seed model is already enabled; inspect the owning verification"
                )
            row.status = ModelRegistrationStatus.ENABLED
        else:
            row = LlmRegisteredModel(
                provider=ProviderName.OPENAI,
                model_identifier=SEED_MODEL,
                display_name="LOCAL VERIFICATION SEED — no provider request",
                metadata_json={"verification_seed": True},
                reasoning_supported_efforts_json=["medium"],
                reasoning_effort_mapping_json={"medium": "medium"},
                reasoning_supports_disable=True,
                reasoning_default_enabled=True,
                reasoning_default_effort="medium",
                reasoning_profile_version="verification-seed.v1",
            )
            db.add(row)
        catalog = await db.scalar(
            select(LlmModelCatalogVersion).where(
                LlmModelCatalogVersion.provider == "openai",
                LlmModelCatalogVersion.model == SEED_MODEL,
            )
        )
        if catalog is None:
            db.add(
                LlmModelCatalogVersion(
                    provider="openai",
                    model=SEED_MODEL,
                    catalog_version="verification-seed.v1",
                    source="local-verification",
                    confirmed_at=utc_now(),
                    context_window=163840,
                    supports_tools=True,
                    tokenizer_name="utf8_bytes_upper_bound",
                    tokenizer_version="v1",
                    capabilities_json={"verification_seed": True},
                )
            )
        elif not (catalog.capabilities_json or {}).get("verification_seed"):
            raise RuntimeError(
                "Existing model catalog is not owned by this verification"
            )
        await db.commit()
        identity = row.model_id
    try:
        yield
    finally:
        async with session_factory() as db:
            row = await db.get(LlmRegisteredModel, identity)
            if row is None or not (row.metadata_json or {}).get("verification_seed"):
                raise RuntimeError(
                    "Seed registration ownership changed; refuse cleanup"
                )
            row.status = ModelRegistrationStatus.DISABLED
            await db.commit()


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
            and self.workflow_round == 1
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
