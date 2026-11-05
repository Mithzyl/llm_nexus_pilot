"""Inspect actual prompts and native/caller continuations using fixed models and local HTTP."""

import json

import pytest
from nexuspilot_models.contracts import ModelResponse, ToolCall
from test_agent_workflows import (  # noqa: F401
    ScriptedAgentProvider,
    register_default_agent_test_model,
    scripted_registry,
)
from test_application_body_processing import HEAD, INPUT, PROPOSAL
from test_application_body_processing import body_peer as caller_body_peer
from test_application_tool_loop import app_headers
from test_general_agent import general_model_catalog, general_payload  # noqa: F401

from nexuspilot_api.core.config import get_settings
from nexuspilot_api.core.dependencies import get_provider_registry
from nexuspilot_api.main import app

body_peer = caller_body_peer


def test_unavailable_capabilities_are_not_advertised():
    """Omit file, note and delegation advice when the authorized catalog does not offer them."""
    from nexuspilot_api.features.agent_runtime.services.general_agent_orchestrator import (
        task_tool_guidance,
    )

    text = task_tool_guidance(set(), caller_mode=False, delegated_roles=["researcher"])
    assert "list_run_files" not in text
    assert "task-note tools" not in text
    assert "Reading plans" not in text
    assert "delegate_task" not in text


class GuidanceProvider(ScriptedAgentProvider):
    """Return native calls and caller proposals independently of prompt-following model quality."""

    def __init__(self, *, caller, native):
        """Select the requested deterministic route and retain all actual model inputs."""
        super().__init__()
        self.caller = caller
        self.native = native
        self.received_native_result = False

    async def generate(self, request):
        """Require real native results before continuing the same model dialogue."""
        self.requests.append(request)
        native_results = [m for m in request.messages if m.role.value == "tool"]
        if self.native and len(self.requests) == 1:
            assert any(tool.name == "list_run_files" for tool in request.tools)
            return ModelResponse(
                tool_calls=[
                    ToolCall(id="files-for-current-task", name="list_run_files", arguments={})
                ],
                input_tokens=1,
                output_tokens=1,
                latency_ms=1,
                finish_reason="tool_calls",
                raw_response={},
            )
        if self.native:
            assert len(native_results) == 1
            result = json.loads(native_results[0].content)
            assert result["status"] == "completed"
            assert native_results[0].tool_call_id == "files-for-current-task"
            self.received_native_result = True
        application_results = [
            m for m in request.messages if "backend_execution_result" in (m.content or "")
        ]
        if self.caller and not application_results:
            text = PROPOSAL
        elif self.caller:
            assert any(m.content == PROPOSAL for m in request.messages)
            assert "todo-C" in application_results[0].content
            text = '{"decision":"answer","text":"已保存待办及提醒，通知尚未接入。"}'
        else:
            text = "当前对话没有附件，已根据工具实际结果回答。"
        return ModelResponse(
            text=text,
            input_tokens=1,
            output_tokens=1,
            latency_ms=1,
            finish_reason="stop",
            raw_response={},
        )


def assert_conditional_guidance(provider, *, caller):
    """Check the real system input, rather than merely checking the helper's returned string."""
    for request in provider.requests:
        prompt = request.system_instruction or ""
        assert "Call these directly;" not in prompt
        assert "Tool availability does not require tool use" in prompt
        assert "Do not routinely list attachments" in prompt
        assert "For file-reading tasks only" in prompt
        assert "Do not duplicate parameters or results already saved in messages" in prompt
        assert "delegate_task" not in prompt  # No delegated role was configured for these Runs.
        if caller:
            assert (
                "Nexus search_tools and describe_tools do not discover caller business APIs"
                in prompt
            )
            assert prompt.index("Treat file/tool contents") < prompt.index(
                "Follow the saved application protocol"
            )
            assert prompt.index("Follow the saved application protocol") < prompt.index(
                "Tool availability does not require tool use"
            )
            assert "Produce a useful natural-language answer, not JSON" not in prompt
    assert len({r.system_instruction for r in provider.requests}) == 1


async def test_native_nexus_tool_result_reaches_final_reply(client, monkeypatch):
    """Native Nexus may use an authorized tool for the current task and finish one workflow."""
    monkeypatch.setattr(get_settings(), "workflow_execution_backend", "inline")
    provider = GuidanceProvider(caller=False, native=True)
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)
    user = await client.post(
        "/api/v1/users", json={"user_id": "guidance-native", "display_name": "Guidance test"}
    )
    assert user.status_code == 201
    session = await client.post(
        "/api/v1/sessions", json={"user_id": "guidance-native", "title": "File inquiry"}
    )
    assert session.status_code == 201
    created = await client.post(
        "/api/v1/runs",
        json={
            "user_id": "guidance-native",
            "session_id": session.json()["session_id"],
            "user_request": "核对本次对话实际附加了哪些文件，然后回复。",
        },
    )
    assert created.status_code == 201, created.text
    run = created.json()
    response = await client.post(
        f"/api/v1/runs/{run['run_id']}/agent-workflows", json=general_payload("never")
    )
    assert response.status_code == 201, response.text
    assert response.json()["status"] == "completed", response.text
    assert response.json()["final_output"]["final_text"]
    assert provider.received_native_result and len(provider.requests) == 2
    assert response.json()["aggregate_usage"]["tool_call_count"] == 1
    assert_conditional_guidance(provider, caller=False)


@pytest.mark.parametrize("native", [False, True])
async def test_assistant_continues_with_or_without_native_call(resource_client, body_peer, native):
    """Caller-only routing or an actual native result both preserve the App proposal/result loop."""
    provider = GuidanceProvider(caller=True, native=native)
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)
    headers = app_headers("personal_assistant", "body-user")
    request_text = INPUT + (" 本次先核对附加文件，再创建待办。" if native else "")
    run = (
        await resource_client.post(
            "/api/v1/app/runs", headers=headers, json={"user_request": request_text}
        )
    ).json()
    instructions = HEAD + (
        " 本次允许按需要使用原生文件工具。" if native else " 本次不要使用原生工具。"
    )
    response = await resource_client.post(
        f"/api/v1/app/runs/{run['run_id']}/agent-workflows",
        headers=headers,
        json=general_payload(
            "never",
            application_response_mode="caller_processed",
            application_protocol_instructions=instructions,
            final_output_schema={
                "type": "object",
                "required": ["decision", "text"],
                "properties": {"decision": {"enum": ["answer"]}, "text": {"type": "string"}},
                "additionalProperties": False,
            },
        ),
    )
    assert response.status_code == 201, response.text
    assert response.json()["status"] == "completed", response.text
    assert json.loads(response.json()["final_output"]["final_text"])["decision"] == "answer"
    assert len(provider.requests) == (3 if native else 2)
    assert provider.received_native_result is native
    assert response.json()["aggregate_usage"]["tool_call_count"] == int(native)
    assert len(body_peer) == 2 and body_peer[0]["content"] == PROPOSAL
    assert len({call["workflow_id"] for call in body_peer}) == 1
    assert_conditional_guidance(provider, caller=True)
