"""Run automatic memory tools through the App Agent loop with a fixed model only."""

import json

import pytest
from nexuspilot_models.contracts import ModelResponse, StreamEvent, StreamEventType, ToolCall
from test_agent_workflows import register_default_agent_test_model, scripted_registry  # noqa: F401
from test_application_tool_loop import (  # noqa: F401
    app_headers,
    configured_application,
    remote_server,
)
from test_general_agent import general_model_catalog, general_payload  # noqa: F401
from test_product_memory import memory_key  # noqa: F401

from nexuspilot_api.core.dependencies import get_provider_registry
from nexuspilot_api.main import app


class AutomaticMemoryProvider:
    """Save a sourced fact, query it, then require restored plaintext before answering."""

    provider_name = "openai"

    def __init__(self):
        """Count the observable rounds for duplicate-execution assertions."""
        self.calls = 0

    async def generate(self, request):
        """Emit deterministic tools without requiring an explicit remember command."""
        self.calls += 1
        if self.calls == 1:
            return ModelResponse(
                tool_calls=[
                    ToolCall(
                        id="auto-save",
                        name="save_memory",
                        arguments={"text": "家庭地址为测试路", "source_quote": "我家在测试路"},
                    )
                ],
                finish_reason="tool_calls",
                input_tokens=0,
                output_tokens=0,
                latency_ms=0,
                raw_response={"fixture": True},
            )
        if self.calls == 2:
            return ModelResponse(
                tool_calls=[
                    ToolCall(id="auto-read", name="query_memory", arguments={"query": "地址"})
                ],
                finish_reason="tool_calls",
                input_tokens=0,
                output_tokens=0,
                latency_ms=0,
                raw_response={"fixture": True},
            )
        assert "家庭地址为测试路" in json.dumps(
            [m.model_dump(mode="json") for m in request.messages], ensure_ascii=False
        )
        return ModelResponse(
            text="已读取保存的事实",
            finish_reason="stop",
            input_tokens=0,
            output_tokens=0,
            latency_ms=0,
            raw_response={"fixture": True},
        )

    async def stream(self, request):
        """Expose the same fixed response through the runtime streaming protocol."""
        yield StreamEvent(type=StreamEventType.STARTED, sequence=1)
        response = await self.generate(request)
        yield StreamEvent(
            type=StreamEventType.COMPLETED,
            sequence=2,
            data={"response": response.model_dump(mode="json")},
        )


@pytest.mark.usefixtures("memory_key", "configured_application")
async def test_app_agent_saves_then_recalls_without_manual_consent(resource_client, monkeypatch):
    """Exercise saved tools and version receipts inline, independent of the local Worker setting."""
    from nexuspilot_api.core.config import get_settings

    monkeypatch.setattr(get_settings(), "workflow_execution_backend", "inline")
    headers = app_headers("personal_assistant", "auto-memory-user")
    created = await resource_client.post(
        "/api/v1/app/runs", headers=headers, json={"user_request": "我家在测试路"}
    )
    assert created.status_code == 201, created.text
    provider = AutomaticMemoryProvider()
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)
    response = await resource_client.post(
        f"/api/v1/app/runs/{created.json()['run_id']}/agent-workflows",
        headers=headers,
        json=general_payload("never"),
    )
    assert response.status_code == 201, response.text
    assert response.json()["status"] == "completed", response.text
    assert response.json()["final_output"]["final_text"]
    assert provider.calls == 3
    path = f"/api/v1/app/runs/{created.json()['run_id']}/diagnostics"
    diagnostics = await resource_client.get(path, headers=headers)
    assert diagnostics.status_code == 200
    writes = [item["memory_write"] for item in diagnostics.json()["tools"]["items"]
              if item.get("memory_write")]
    assert len(writes) == 1
    write = writes[0]
    assert write["applied_version"] == write["current_version"] == 1
    assert write["status"] == "active" and write["can_undo"] is True
    assert "测试路" not in diagnostics.text
    changed = await resource_client.post("/api/v1/app/me/user-memories", headers=headers,
        json={"action": "correct", "key": "later-edit", "entry_id": write["entry_id"],
              "expected_version": 1, "text": "新的非敏感偏好", "confirmed_sensitive": True})
    assert changed.status_code == 200, changed.text
    refreshed = (await resource_client.get(path, headers=headers)).json()
    later = next(item["memory_write"] for item in refreshed["tools"]["items"]
                 if item.get("memory_write"))
    assert later["applied_version"] == 1 and later["current_version"] == 2
    assert later["can_undo"] is False
