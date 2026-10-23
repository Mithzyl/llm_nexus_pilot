"""Verify OpenCode routing without paid requests or official DeepSeek credentials."""

import json

import httpx
import pytest
from nexuspilot_models.contracts import ModelRequest, ProviderName, StreamEventType

from nexuspilot_api.core.config import Settings
from nexuspilot_api.infrastructure.provider_registry import create_provider_registry


@pytest.mark.parametrize("stream", [False, True])
async def test_opencode_routes_chat_and_preserves_usage(stream):
    """Use the documented endpoint/model, authenticate only in headers and decode both modes."""
    provider = ProviderName("opencode")
    calls = []

    def respond(request):
        """Return deterministic Chat JSON or SSE; never call an external network."""
        payload = json.loads(request.content)
        calls.append(payload)
        assert str(request.url) == "https://opencode.ai/inference/go/openai/v1/chat/completions"
        assert request.headers["User-Agent"] == "NexusPilot/0.1"
        assert request.headers["x-opencode-session"]
        assert request.headers["authorization"] == "Bearer local-test-key"
        assert payload["model"] == "deepseek-v4.1-flash"
        assert "local-test-key" not in str(payload)
        assert "thinking" not in payload and "reasoning_effort" not in payload
        body = {
            "choices": [
                {"delta" if stream else "message": {"content": "OK"}, "finish_reason": "stop"}
            ],
            "usage": {"prompt_tokens": 11, "completion_tokens": 3},
        }
        if stream:
            return httpx.Response(
                200,
                text="data: " + json.dumps(body) + "\n\ndata: [DONE]\n\n",
                headers={"content-type": "text/event-stream"},
            )
        return httpx.Response(200, json=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        registry = create_provider_registry(
            Settings(
                _env_file=None,
                opencode_api_key="local-test-key",
                opencode_models="deepseek-v4.1-flash",
            ),
            client,
            {provider: "https://opencode.ai/inference/go/openai/v1"},
        )
        adapter = registry.resolve(provider, "deepseek-v4.1-flash")
        assert not adapter.supports_json_schema
        request = ModelRequest(
            provider=provider,
            model="deepseek-v4.1-flash",
            messages=[{"role": "user", "content": "test"}],
        )
        if stream:
            events = [event async for event in adapter.stream(request)]
            result = next(
                event.data["response"]
                for event in events
                if event.type is StreamEventType.COMPLETED
            )
            assert result["text"] == "OK" and result["input_tokens"] == 11
        else:
            result = await adapter.generate(request)
            assert result.text == "OK" and result.output_tokens == 3
        assert len(calls) == 1


@pytest.mark.parametrize("key,endpoint", [(None, True), ("local-test-key", False)])
async def test_opencode_requires_own_key_and_endpoint(key, endpoint):
    """A DeepSeek credential alone must not enable OpenCode or silently route to it."""
    provider = ProviderName("opencode")
    async with httpx.AsyncClient() as client:
        registry = create_provider_registry(
            Settings(_env_file=None, opencode_api_key=key, deepseek_api_key="different-key"),
            client,
            {provider: "https://opencode.ai/inference/go/openai/v1"} if endpoint else {},
        )
        assert provider not in registry.registered_names


async def test_opencode_tool_round_preserves_private_reasoning():
    """Replay returned reasoning_content on its assistant tool turn, not in user text."""
    from nexuspilot_models.contracts import Message

    requests = []

    def respond(request):
        """Return one tool call then verify the next HTTP payload before a final response."""
        payload = json.loads(request.content)
        assert request.headers["x-opencode-session"] == "workflow-stable-id"
        requests.append(payload)
        if len(requests) == 1:
            message = {
                "role": "assistant",
                "content": None,
                "reasoning_content": "private continuation",
                "tool_calls": [
                    {
                        "id": "t1",
                        "type": "function",
                        "function": {"name": "read", "arguments": "{}"},
                    }
                ],
            }
            return httpx.Response(
                200, json={"choices": [{"message": message, "finish_reason": "tool_calls"}]}
            )
        assert payload["messages"][1]["reasoning_content"] == "private continuation"
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "done"}, "finish_reason": "stop"}]}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        provider = ProviderName.OPENCODE
        registry = create_provider_registry(
            Settings(
                _env_file=None, opencode_api_key="test-key", opencode_models="deepseek-v4.1-flash"
            ),
            client,
            {provider: "https://opencode.ai/inference/go/openai/v1"},
        )
        adapter = registry.resolve(provider, "deepseek-v4.1-flash")
        user = Message(role="user", content="read")
        first = await adapter.generate(
            ModelRequest(
                provider=provider,
                model="deepseek-v4.1-flash",
                messages=[user],
                metadata={"workflow_execution_id": "workflow-stable-id"},
            )
        )
        assert first.provider_continuation_state is not None
        assistant = Message(role="assistant", content="", tool_calls=first.tool_calls)
        assistant._provider_continuation_state = first.provider_continuation_state
        second = await adapter.generate(
            ModelRequest(
                provider=provider,
                model="deepseek-v4.1-flash",
                metadata={"workflow_execution_id": "workflow-stable-id"},
                messages=[
                    user,
                    assistant,
                    Message(role="tool", tool_call_id="t1", content="result"),
                ],
            )
        )
        assert second.text == "done" and len(requests) == 2
