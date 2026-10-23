"""Verify opt-in interrupted-text continuation without paid calls or tool replay."""

import pytest
from nexuspilot_models.contracts import ModelResponse, ToolCall
from nexuspilot_models.errors import ModelProviderError
from sqlalchemy import select
from test_agent_workflows import register_default_agent_test_model, scripted_registry  # noqa: F401
from test_api import create_test_run
from test_general_agent import GeneralProvider, general_model_catalog, general_payload  # noqa: F401

from nexuspilot_api.core.dependencies import get_provider_registry
from nexuspilot_api.main import app
from nexuspilot_api.models import LlmAgentWorkflowEvent, LlmModelAttempt, LlmModelToolCall


def test_continuation_policy_preserves_default_and_rejects_legacy():
    """The opt-in cannot silently change old requests or enable a legacy workflow."""
    from pydantic import ValidationError

    from nexuspilot_api.features.agent_runtime.schemas.agent_workflows import AgentWorkflowCreate
    from nexuspilot_api.features.agent_runtime.services.workflow_policy import hash_workflow_request

    default = AgentWorkflowCreate.model_validate(general_payload())
    explicit = default.model_copy(update={"partial_response_policy": "stop"})
    assert hash_workflow_request(default) == hash_workflow_request(explicit)
    enabled = default.model_copy(update={"partial_response_policy": "continue_once"})
    assert hash_workflow_request(default) != hash_workflow_request(enabled)
    legacy = general_payload(partial_response_policy="continue_once")
    legacy.update(workflow_name="model_only", execution_profile="model_only_v1")
    with pytest.raises(ValidationError, match="requires general_agent"):
        AgentWorkflowCreate.model_validate(legacy)


@pytest.mark.parametrize("policy,text,fragments,allowed", [
    ("continue_once", "A partial explanation", [], True),
    ("stop", "A partial explanation", [], False),
    ("continue_once", "", [], False),
    ("continue_once", "A partial explanation", [{"arguments_delta": "{"}], False),
])
@pytest.mark.parametrize("review_policy", ["never", "always", "on_verification_failure"])
async def test_general_agent_continues_only_authorized_text(
    client, test_database_session_factory, policy, text, fragments, allowed, review_policy,
):
    """Retain a failed Attempt and use saved text only when explicitly opted in."""
    class InterruptedProvider(GeneralProvider):
        """Fail once with received text, then return a complete replacement answer."""

        async def generate(self, request):
            """Expose no hidden reasoning and require explicit incomplete-draft labeling."""
            if request.metadata["agent_role"] == "reviewer":
                return await super().generate(request)
            self.requests.append(request)
            if len(self.requests) == 1:
                error = ModelProviderError("network_error", "Stream disconnected")
                error.stream_progress = {
                    "schema_version": "model_stream_progress.v1",
                    "response_complete": False, "text": text,
                    "tool_fragments": fragments, "usage": {}, "elapsed_ms": 1,
                }
                raise error
            assert any("interrupted_draft" in (m.content or "") for m in request.messages)
            assert any(text in (m.content or "") for m in request.messages)
            return ModelResponse(text="Complete replacement answer.", finish_reason="stop",
                                 input_tokens=10, output_tokens=4, latency_ms=1,
                                 raw_response={"text": "Complete replacement answer."})

    provider = InterruptedProvider()
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)
    run = await create_test_run(client)
    response = await client.post(f"/api/v1/runs/{run['run_id']}/agent-workflows",
                                json=general_payload(review_policy, partial_response_policy=policy))
    assert response.status_code == 201, response.text
    result = response.json()
    assert (result["status"] == "completed") is allowed, (result["error"], len(provider.requests))
    assert len(provider.requests) == (2 + int(review_policy == "always") if allowed else 1)
    async with test_database_session_factory() as db:
        attempts = list(await db.scalars(select(LlmModelAttempt).where(
            LlmModelAttempt.run_id == run["run_id"],
        ).order_by(LlmModelAttempt.started_at)))
        assert attempts[0].status.value == "failed"
        assert attempts[0].raw_response_uri
        if allowed:
            assert attempts[-1].status.value == "completed"
            assert result["final_output"]["final_text"] == "Complete replacement answer."


@pytest.mark.parametrize("repeat_failure,expire", [(False, False), (True, False), (False, True)])
async def test_continuation_preserves_tools_and_stops_on_repeat_or_expiry(
    client, test_database_session_factory, monkeypatch, repeat_failure, expire,
):
    """Successful tools are not replayed; a second interruption or expired deadline stops work."""
    class Provider(GeneralProvider):
        """Read the file catalog once, then interrupt a draft before returning the answer."""

        async def generate(self, request):
            """Keep invocation sequence observable without external network traffic."""
            self.requests.append(request)
            if len(self.requests) == 1:
                return ModelResponse(tool_calls=[ToolCall(
                    id="catalog-1", name="list_run_files", arguments={},
                )], finish_reason="tool_calls", input_tokens=2, output_tokens=2,
                    latency_ms=1, raw_response={})
            if len(self.requests) == 2 or repeat_failure:
                if expire:
                    monkeypatch.setattr(
                        "nexuspilot_api.features.agent_runtime.services.agent_tool_loop.monotonic",
                        lambda: 10**20,
                    )
                error = ModelProviderError("timeout", "Request interrupted")
                error.stream_progress = {
                    "schema_version": "model_stream_progress.v1", "response_complete": False,
                    "text": "Draft based on saved file catalog", "tool_fragments": [],
                    "usage": {}, "elapsed_ms": 1,
                }
                raise error
            assert len([m for m in request.messages if m.role.value == "tool"]) == 1
            return ModelResponse(text="No files were provided.", finish_reason="stop",
                                 input_tokens=5, output_tokens=4, latency_ms=1, raw_response={})

    provider = Provider()
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)
    run = await create_test_run(client)
    response = await client.post(f"/api/v1/runs/{run['run_id']}/agent-workflows",
        json=general_payload(partial_response_policy="continue_once"))
    assert response.status_code == 201
    assert (response.json()["status"] == "completed") is (not repeat_failure and not expire)
    assert len(provider.requests) == (2 if expire else 3)
    async with test_database_session_factory() as db:
        attempts = list(await db.scalars(select(LlmModelAttempt.attempt_id).where(
            LlmModelAttempt.run_id == run["run_id"],
        )))
        tools = list(await db.scalars(select(LlmModelToolCall).where(
            LlmModelToolCall.attempt_id.in_(attempts),
        )))
        assert len(tools) == 1
        events = list(await db.scalars(select(LlmAgentWorkflowEvent).where(
            LlmAgentWorkflowEvent.run_id == run["run_id"],
            LlmAgentWorkflowEvent.event_type == "agent.draft.continuation.requested",
        )))
        assert len(events) == (0 if expire else 1)
