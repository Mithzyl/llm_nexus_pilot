"""Verify both real execution loops yield to a human and resume saved calls without repetition."""

import base64

import pytest
from conftest import fake_price_catalog
from nexuspilot_models.contracts import ModelResponse, StreamEvent, StreamEventType, ToolCall
from pydantic import SecretStr
from test_agent_workflows import (  # noqa: F401
    ScriptedAgentProvider,
    register_default_agent_test_model,
    scripted_registry,
)
from test_api import create_test_run
from test_general_agent import (  # noqa: F401
    general_model_catalog,
    general_payload,
    unit_review_text,
)

from nexuspilot_api.core.config import get_settings
from nexuspilot_api.models import LlmRun
from nexuspilot_api.schemas.responses import ResponsesRequest
from nexuspilot_api.schemas.sessions import MessageCreate
from nexuspilot_api.services.session_service import create_message
from nexuspilot_api.services.user_interactions import list_interactions, reply_to_interaction


class HumanInputProvider(ScriptedAgentProvider):
    """Ask one question then answer only after the actual persisted Tool reply exists."""

    async def generate(self, request):
        """Produce deterministic native calls without a provider HTTP request."""
        self.requests.append(request)
        if request.metadata.get("agent_role") == "reviewer":
            return ModelResponse(
                text=unit_review_text(request, "supported", "Checked fixed user reply"),
                finish_reason="stop",
                input_tokens=1,
                output_tokens=1,
                latency_ms=1,
                raw_response={},
            )
        history = [m for m in request.messages if m.role.value == "tool"]
        if history:
            assert "Paris" in history[-1].content
            return ModelResponse(
                text="The user selected Paris.",
                finish_reason="stop",
                input_tokens=1,
                output_tokens=1,
                latency_ms=1,
                raw_response={},
            )
        assert "request_user_input" in {tool.name for tool in request.tools}
        return ModelResponse(
            tool_calls=[
                ToolCall(
                    id="human-question",
                    name="request_user_input",
                    arguments={"kind": "input", "question": "Which city?"},
                )
            ],
            finish_reason="tool_calls",
            input_tokens=1,
            output_tokens=1,
            latency_ms=1,
            raw_response={},
        )

    async def stream(self, request):
        """Emit the same fixed model response through the streaming adapter contract."""
        yield StreamEvent(type=StreamEventType.STARTED, sequence=1)
        result = await self.generate(request)
        yield StreamEvent(
            type=StreamEventType.COMPLETED,
            sequence=2,
            data={"response": result.model_dump(mode="json")},
        )


@pytest.mark.parametrize(
    "mode,policy",
    [
        ("quick", "never"),
        ("agent", "never"),
        ("agent", "always"),
        ("agent", "on_verification_failure"),
    ],
)
async def test_execution_waits_and_resumes_same_human_call(
    client, test_database_session_factory, monkeypatch, mode, policy
):
    """Resume two generation calls and only the reviewer calls required by the saved policy."""
    from nexuspilot_api.features.agent_runtime.services.workflow_ownership import claim_workflow
    from nexuspilot_api.features.agent_runtime.services.workflow_worker import execute_claim
    from nexuspilot_api.features.memory.models.product_memory import MemoryRunPermission
    from nexuspilot_api.models import LlmAgentWorkflowExecution
    from nexuspilot_api.models.quick_response import QuickResponseExecution
    from nexuspilot_api.services.quick_response_submission import accept_quick_response
    from nexuspilot_api.services.quick_response_worker import execute_quick_response

    monkeypatch.setattr(get_settings(), "workflow_execution_backend", "worker")
    monkeypatch.setattr(
        get_settings(),
        "memory_encryption_key",
        SecretStr(base64.urlsafe_b64encode(b"x" * 32).decode()),
    )
    created = await create_test_run(client)
    provider = HumanInputProvider()
    registry = scripted_registry(provider)
    async with test_database_session_factory() as db:
        run = await db.get(LlmRun, created["run_id"])
        owner, storage = run.user_id, db.body_storage
        if mode == "quick":
            message = await create_message(
                db,
                run.session_id,
                MessageCreate(role="user", run_id=run.run_id, content_text="Ask me for a city"),
            )
            permission = await db.get(MemoryRunPermission, run.run_id)
            assert permission is not None
            permission.allowed = True
            await db.commit()
            accepted = await accept_quick_response(
                db,
                storage,
                owner,
                ResponsesRequest(
                    run_id=run.run_id,
                    current_user_message_id=message.message_id,
                    input="Ask me for a city",
                    provider="openai",
                    model="test-model",
                    idempotency_key="human-input-execution",
                    timeout_seconds=None,
                    stream=True,
                ),
            )
            identity = accepted.response_execution_id
    if mode == "agent":
        response = await client.post(
            f"/api/v1/runs/{created['run_id']}/agent-workflows", json=general_payload(policy)
        )
        assert response.status_code == 201, response.text
        identity = response.json()["workflow_execution_id"]

    async def consume():
        """Use a fresh execution claim after each durable reply."""
        if mode == "quick":
            await execute_quick_response(
                test_database_session_factory, storage, registry, fake_price_catalog(), identity
            )
        else:
            claim = await claim_workflow(test_database_session_factory, identity, "test-worker")
            assert claim
            await execute_claim(
                test_database_session_factory, storage, registry, fake_price_catalog(), claim
            )

    await consume()
    async with test_database_session_factory() as db:
        questions = await list_interactions(db, storage, owner, created["run_id"])
        assert len(questions) == 1
        assert questions[0]["status"] == "pending"
        assert len(provider.requests) == 1
        await reply_to_interaction(
            db,
            storage,
            owner,
            created["run_id"],
            questions[0]["interaction_id"],
            {"expected_version": 1, "key": "actual-user-reply", "text": "Paris"},
        )
    await consume()
    async with test_database_session_factory() as db:
        record = await db.get(
            QuickResponseExecution if mode == "quick" else LlmAgentWorkflowExecution, identity
        )
        assert str(record.status) in {"completed", "AgentWorkflowStatus.COMPLETED"}
    assert len(provider.requests) == (3 if policy == "always" else 2)
