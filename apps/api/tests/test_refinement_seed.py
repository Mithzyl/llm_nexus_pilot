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
    with seed_module.seed_first_turn(Orchestrator, run_id, binding):
        result = await Orchestrator()._turn(
            SimpleNamespace(run_id=run_id if scope == "target" else str(uuid4())),
            None,
            payload,
            SimpleNamespace(assigned_role=role),
            None,
            "",
            {"workflow_round": round_number},
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
