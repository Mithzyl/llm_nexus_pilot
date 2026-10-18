"""Specify progress-based continuation without a fixed default model-call ceiling."""

import json

import pytest
from nexuspilot_models.contracts import ModelResponse, ToolCall
from test_agent_workflows import (
    ScriptedAgentProvider,
    register_default_agent_test_model,  # noqa: F401
    scripted_registry,
    workflow_payload,
)
from test_context_builds import create_model_capability
from test_user_files import reserve_file

from nexuspilot_api.core.dependencies import get_provider_registry
from nexuspilot_api.features.agent_runtime.schemas.agent_workflows import AgentWorkflowCreate
from nexuspilot_api.main import app
from nexuspilot_api.services.tool_context import ToolProgressTracker


class LongReadProvider(ScriptedAgentProvider):
    """Return twenty real successive file reads before answering, with no paid model."""

    def __init__(self, file_id, mode):
        """Bind the uploaded fixture and distinguish quick requests from workflow nodes."""
        super().__init__()
        self.file_id = file_id
        self.mode = mode
        self.reads = 0

    async def generate(self, request):
        """Drive real registered tools; return the existing valid schema after useful reads."""
        is_worker = request.metadata.get("agent_node_key") == "worker_execution"
        if self.mode == "quick" or is_worker:
            assert request.tools, "Tools must not disappear at an old fixed call boundary"
            if self.reads < 20:
                self.requests.append(request)
                index = self.reads
                self.reads += 1
                return ModelResponse(
                    tool_calls=[
                        ToolCall(
                            id=f"read-{index}",
                            name="read_file_content",
                            arguments={"file_id": self.file_id, "offset": index, "limit": 1},
                        )
                    ],
                    finish_reason="tool_calls",
                    input_tokens=10,
                    output_tokens=5,
                    latency_ms=1,
                    raw_response={"fixture": "read"},
                )
            if self.mode == "quick":
                self.requests.append(request)
                return ModelResponse(
                    text="Read twenty blocks",
                    finish_reason="stop",
                    input_tokens=10,
                    output_tokens=5,
                    latency_ms=1,
                    raw_response={"fixture": "answer"},
                )
            reference = json.loads(request.messages[-1].content)["result"]["evidence_ref"]
            output = {"schema_version": "worker_evidence.v2",
                      "summary": "Read twenty blocks", "confirmed_facts": [], "decisions": [],
                      "remaining_work": [], "risks": [], "unknowns": [],
                      "claims": [{"claim_key": "last-line", "text": "Unique line 19",
                                  "evidence_refs": [reference]}],
                      "unresolved_items": [], "hypotheses": []}
            self.requests.append(request)
            return ModelResponse(text=json.dumps(output), structured_output=output,
                finish_reason="stop", input_tokens=10, output_tokens=5, latency_ms=1,
                raw_response={"fixture": "cited-answer"})
        return await super().generate(request)


@pytest.mark.parametrize("mode", ["quick", "agent"])
async def test_useful_reads_continue_past_eight_and_sixteen_calls(client, mode):
    """Real API/SQL/tool paths allow twenty new ranges and a final answer under default policy."""
    await create_model_capability(client, context_window=65536)
    owner = (await client.get("/api/v1/auth/me")).json()["user_id"]
    session = (await client.post("/api/v1/sessions", json={"user_id": owner})).json()
    content = "\n".join(f"Unique line {index}" for index in range(30)).encode()
    file = (await reserve_file(client, content)).json()
    uploaded = await client.put(f"/api/v1/files/{file['file_id']}/content", content=content)
    assert uploaded.status_code == 200
    turn = (
        await client.post(
            f"/api/v1/sessions/{session['session_id']}/turns",
            json={
                "user_id": owner,
                "content_text": "Read twenty lines",
                "file_ids": [file["file_id"]],
            },
        )
    ).json()
    provider = LongReadProvider(file["file_id"], mode)
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)
    if mode == "quick":
        response = await client.post(
            "/api/v1/responses",
            json={
                "run_id": turn["run"]["run_id"],
                "current_user_message_id": turn["message"]["message_id"],
                "provider": "openai",
                "model": "test-model",
                "input": "Read twenty lines",
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["output_text"] == "Read twenty blocks"
    else:
        response = await client.post(
            f"/api/v1/runs/{turn['run']['run_id']}/agent-workflows",
            json=workflow_payload(execution_profile="tool_enabled_v1", workflow_version="1.0.0",
                                  review_policy="never"),
        )
        assert response.status_code == 201, response.text
        assert response.json()["status"] == "completed", json.dumps(response.json().get("error"))
        assert response.json()["max_model_calls"] == 0
        assert response.json()["model_call_count"] > 16
    assert provider.reads == 20


def test_new_workflow_has_no_default_call_limit():
    """New requests default to unlimited call count, not an implicit sixteen-call budget."""
    binding = {"provider": "strata", "model": "test"}
    request = AgentWorkflowCreate(
        idempotency_key="unlimited-test",
        role_bindings={role: binding for role in ["controller", "planner", "reviewer"]},
    )
    assert request.max_model_calls == 0


def test_new_blocks_continue_beyond_old_limits_but_repeated_results_stop():
    """Changed offsets cannot disguise repeatedly returning the same original blocks."""
    tracker = ToolProgressTracker()
    for index in range(40):
        result = {
            "status": "completed",
            "result": {
                "file_id": "file",
                "parsed_hash": "hash",
                "data": {"blocks": [{"block_id": str(index), "text": "source"}]},
            },
        }
        assert tracker.observe("read_file_content", {"offset": index}, result)
    assert tracker.observe("read_file_content", {"offset": 41}, result)
    assert tracker.observe("read_file_content", {"offset": 42}, result)
    assert not tracker.observe("read_file_content", {"offset": 43}, result)
