"""Check the opt-in verification seed without networking or real database writes."""

import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from nexuspilot_models.contracts import Message, ModelRequest, ProviderName
from test_general_agent import general_payload

from nexuspilot_api.features.agent_runtime.schemas.agent_workflows import AgentWorkflowCreate


@pytest.fixture
def seed_module(monkeypatch):
    """Load only the verification module; importing it must not install an API hook."""
    monkeypatch.syspath_prepend(
        str(Path(__file__).resolve().parents[3] / "deployments/verification")
    )
    import refinement_seed

    return refinement_seed


async def test_seed_registration_disables_after_failure_and_reuses_catalog(
    seed_module,
    test_database_session_factory,
):
    """Retain audit rows, disable the fixture on exit, and reuse its catalog version."""
    from sqlalchemy import func, select

    from nexuspilot_api.models import LlmModelCatalogVersion, LlmRegisteredModel

    with pytest.raises(RuntimeError, match="test interruption"):
        async with seed_module.register_seed(test_database_session_factory):
            async with test_database_session_factory() as db:
                row = await db.scalar(
                    select(LlmRegisteredModel).where(
                        LlmRegisteredModel.model_identifier == seed_module.SEED_MODEL,
                    )
                )
                assert row.status.value == "enabled"
            raise RuntimeError("test interruption")
    async with seed_module.register_seed(test_database_session_factory):
        with pytest.raises(RuntimeError, match="already enabled"):
            async with seed_module.register_seed(test_database_session_factory):
                pass
    async with test_database_session_factory() as db:
        row = await db.scalar(
            select(LlmRegisteredModel).where(
                LlmRegisteredModel.model_identifier == seed_module.SEED_MODEL,
            )
        )
        assert row.status.value == "disabled"
        assert (
            await db.scalar(
                select(func.count())
                .select_from(LlmModelCatalogVersion)
                .where(
                    LlmModelCatalogVersion.model == seed_module.SEED_MODEL,
                )
            )
            == 1
        )


async def test_seed_uses_real_read_and_never_claims_a_strata_request(seed_module):
    """Keep real source selection separate from intentionally incorrect fixture prose."""
    file_id = str(uuid4())
    provider = seed_module.SeedProvider(file_id)
    request = ModelRequest(
        provider=ProviderName.OPENAI,
        model=seed_module.SEED_MODEL,
        messages=[Message(role="user", content="核对学分")],
        metadata={"agent_role": "controller"},
    )
    read = await provider.generate(request)
    assert read.tool_calls[0].arguments["file_id"] == file_id
    request.messages.append(
        Message(
            role="tool",
            tool_name="read_file_content",
            tool_call_id="seed-read",
            content=json.dumps(
                {
                    "status": "completed",
                    "result": {
                        "file_id": file_id,
                        "source_alias": "S7",
                        "data": {"blocks": [{"text": "Degree project 30 credits"}]},
                    },
                }
            ),
        )
    )
    response = await provider.generate(request)
    assert "300 [S7]" in response.text
    assert response.raw_response["record_kind"] == "verification_seed"
    assert response.raw_response["provider_request_sent"] is False
    request.messages[-1].content = json.dumps({"status": "failed", "result": None})
    with pytest.raises(ValueError, match="expected real source"):
        await provider.generate(request)
    request.model = "real-model"
    with pytest.raises(ValueError):
        await provider.generate(request)


@pytest.mark.parametrize(
    "scope,role,round_number,seeded",
    [
        ("target", "controller", 1, True),
        ("other", "controller", 1, False),
        ("target", "reviewer", 1, False),
        ("target", "controller", 2, False),
    ],
)
async def test_seed_hook_is_scoped_and_restored(seed_module, scope, role, round_number, seeded):
    """The fixture never replaces independent review, correction rounds or another Run."""

    class Orchestrator:
        """Capture the selected binding without invoking the application."""

        async def _turn(self, workflow, node, payload, task, state, purpose, values):
            """Return the caller-visible binding for isolation assertions."""
            return payload.role_bindings["controller"].model

    payload = AgentWorkflowCreate.model_validate(general_payload("always"))
    binding = payload.role_bindings["controller"].model_copy(
        update={"model": seed_module.SEED_MODEL}
    )
    run_id = str(uuid4())
    original = Orchestrator._turn
    instance = Orchestrator()
    instance.workflow_round = round_number
    with seed_module.seed_first_turn(Orchestrator, run_id, binding):
        result = await instance._turn(
            SimpleNamespace(run_id=run_id if scope == "target" else str(uuid4())),
            None,
            payload,
            SimpleNamespace(assigned_role=role),
            None,
            "",
            {},
        )
        assert result == (
            seed_module.SEED_MODEL if seeded else payload.role_bindings["controller"].model
        )
        assert payload.role_bindings["controller"].model != seed_module.SEED_MODEL
        with pytest.raises(RuntimeError):
            with seed_module.seed_first_turn(Orchestrator, run_id, binding):
                pass
    assert Orchestrator._turn is original
    with pytest.raises(RuntimeError, match="fixture failure"):
        with seed_module.seed_first_turn(Orchestrator, run_id, binding):
            raise RuntimeError("fixture failure")
    assert Orchestrator._turn is original


async def test_seed_runs_through_persisted_review_and_real_binding_refinement(
    client,
    seed_module,
    test_database_session_factory,
    monkeypatch,
):
    """Exercise actual API persistence with scripted follow-up and distinct seed provenance."""
    from nexuspilot_models.contracts import ModelResponse, ToolCall
    from nexuspilot_models.registry import ProviderRegistry
    from sqlalchemy import select
    from test_agent_workflows import INTERNAL_HEADERS, registered_agent_test_model
    from test_general_agent import unit_review_text
    from test_user_files import reserve_file

    from nexuspilot_api.core.dependencies import get_provider_registry
    from nexuspilot_api.features.agent_runtime.services.general_agent_orchestrator import (
        GeneralAgentOrchestrator,
    )
    from nexuspilot_api.main import app
    from nexuspilot_api.models import LlmModelAttempt

    def expose_failure(self, error):
        """Show fixture assembly errors instead of masking them as an API error envelope."""
        raise error.__cause__ or error

    monkeypatch.setattr(GeneralAgentOrchestrator, "_public_error_from_exception", expose_failure)
    client._transport.raise_app_exceptions = True

    class Followup:
        """Stand in for paid Strata only in this offline API test."""

        def __init__(self):
            """Count actual reviewer invocations separately from seed outputs."""
            self.reviews = 0

        async def generate(self, request):
            """Reject the fixed wrong number, then approve the separately revised answer."""
            assert request.provider == ProviderName.STRATA and request.model == "followup-test"
            if request.metadata["agent_role"] == "reviewer":
                self.reviews += 1
                verdict = "revise" if self.reviews == 1 else "supported"
                args = json.loads(unit_review_text(request, verdict, "原文为30学分，请修正300。"))
                return ModelResponse(
                    tool_calls=[
                        ToolCall(id=f"review-{self.reviews}", name="submit_review", arguments=args)
                    ],
                    finish_reason="tool_calls",
                    input_tokens=10,
                    output_tokens=5,
                    latency_ms=1,
                    raw_response={"fixture": True},
                )
            assert self.reviews == 1
            return ModelResponse(
                text="该论文的Degree project是30学分 [S1]。",
                finish_reason="stop",
                input_tokens=10,
                output_tokens=5,
                latency_ms=1,
                raw_response={"fixture": True},
            )

    async with test_database_session_factory() as db:
        for provider, model in [
            (ProviderName.OPENAI, seed_module.SEED_MODEL),
            (ProviderName.STRATA, "followup-test"),
        ]:
            row = registered_agent_test_model(model)
            row.provider = provider
            if provider == ProviderName.STRATA:
                row.reasoning_supported_efforts_json = []
                row.reasoning_effort_mapping_json = {}
                row.reasoning_supports_disable = False
            db.add(row)
        await db.commit()
    for provider, model in [("openai", seed_module.SEED_MODEL), ("strata", "followup-test")]:
        response = await client.post(
            "/api/v1/internal/model-catalog-versions",
            headers=INTERNAL_HEADERS,
            json={
                "provider": provider,
                "model": model,
                "catalog_version": "seed-test",
                "source": "offline-test",
                "context_window": 65536,
                "supports_tools": True,
                "tokenizer_name": "utf8_bytes_upper_bound",
                "tokenizer_version": "v1",
            },
        )
        assert response.status_code == 200, response.text
    owner = (await client.get("/api/v1/auth/me")).json()["user_id"]
    session = (await client.post("/api/v1/sessions", json={"user_id": owner})).json()
    content = b"Degree project 30 credits"
    file = (await reserve_file(client, content)).json()
    assert (
        await client.put(f"/api/v1/files/{file['file_id']}/content", content=content)
    ).status_code == 200
    turn = (
        await client.post(
            f"/api/v1/sessions/{session['session_id']}/turns",
            json={
                "user_id": owner,
                "content_text": "核对论文的学分",
                "file_ids": [file["file_id"]],
            },
        )
    ).json()
    registry = ProviderRegistry()
    registry.register(
        ProviderName.OPENAI,
        seed_module.SeedProvider(file["file_id"]),
        allowed_models=frozenset({seed_module.SEED_MODEL}),
    )
    followup = Followup()
    registry.register(ProviderName.STRATA, followup, allowed_models=frozenset({"followup-test"}))
    app.dependency_overrides[get_provider_registry] = lambda: registry
    payload = general_payload("always")
    for binding in payload["role_bindings"].values():
        binding.update(provider="strata", model="followup-test")
    seed_binding = (
        AgentWorkflowCreate.model_validate(payload)
        .role_bindings["controller"]
        .model_copy(
            update={"provider": ProviderName.OPENAI, "model": seed_module.SEED_MODEL},
        )
    )
    run_id = turn["run"]["run_id"]
    with seed_module.seed_first_turn(GeneralAgentOrchestrator, run_id, seed_binding):
        response = await client.post(f"/api/v1/runs/{run_id}/agent-workflows", json=payload)
        assert response.status_code == 201, response.text
        result = response.json()
    assert result["status"] == "completed", result.get("error")
    assert result["loop_statistics"]["workflow_round_count"] == 2
    assert result["loop_statistics"]["refinement_count"] == 1
    assert result["model_call_count"] == 5
    assert result["loop_statistics"]["refinement_usage"]["model_call_count"] == 2
    assert "300" not in result["final_output"]["final_text"]
    assert followup.reviews == 2
    async with test_database_session_factory() as db:
        attempts = list(
            await db.scalars(
                select(LlmModelAttempt)
                .where(
                    LlmModelAttempt.run_id == run_id,
                )
                .order_by(LlmModelAttempt.started_at)
            )
        )
        assert [a.provider for a in attempts] == ["openai", "openai", "strata", "strata", "strata"]
        assert [a.model for a in attempts[:2]] == [seed_module.SEED_MODEL] * 2
        for attempt in attempts[:2]:
            assert attempt.input_tokens == 0 and attempt.output_tokens == 0
            stored = await db.body_storage.open_object(attempt.raw_response_uri)
            raw = json.loads(b"".join(stored.chunks))
            assert raw["record_kind"] == "verification_seed"
            assert raw["provider_request_sent"] is False


@pytest.mark.parametrize("paid,existing", [(False, False), (True, True), (True, False)])
async def test_live_runner_refuses_missing_permission_existing_report_or_nonlocal_database(
    seed_module, tmp_path, paid, existing,
):
    """Guard failures happen before upload, model execution, or new report creation."""
    import check_seeded_refinement as runner

    report = tmp_path / "report.json"
    if existing:
        report.write_text("existing verification")
    with pytest.raises(RuntimeError):
        await runner.run(SimpleNamespace(
            allow_paid_strata=paid, report=report, file=tmp_path / "does-not-exist.pdf",
        ))
    assert report.exists() == existing
    if existing:
        assert report.read_text() == "existing verification"
