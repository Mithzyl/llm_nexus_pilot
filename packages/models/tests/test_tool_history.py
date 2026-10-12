"""Check complete multi-round tool wire formats without external Provider calls."""

import json

import httpx
import pytest
from nexuspilot_models.contracts import (
    Message,
    MessageRole,
    ModelRequest,
    ProviderContinuationKind,
    ProviderContinuationState,
    ProviderName,
    StreamEventType,
    ToolCall,
)
from nexuspilot_models.errors import ModelProviderError
from nexuspilot_models.providers.anthropic import AnthropicMessagesProvider
from nexuspilot_models.providers.deepseek import DeepSeekChatProvider
from nexuspilot_models.providers.gemini import GeminiGenerateContentProvider
from nexuspilot_models.providers.openai import OpenAIResponsesProvider
from nexuspilot_models.transport import HttpTransport


def tool_history(provider):
    """Build two assistant/function-result pairs using stable, distinct provider call IDs."""
    messages = [Message(role=MessageRole.USER, content="Read the attached report")]
    for index in range(2):
        call = ToolCall(
            id=f"call-{index}", name="read_file_content", arguments={"offset": index}
        )
        messages.extend(
            [
                Message(role=MessageRole.ASSISTANT, content="", tool_calls=[call]),
                Message(
                    role=MessageRole.TOOL,
                    content='{"status":"completed"}',
                    tool_call_id=call.id,
                    tool_name=call.name,
                ),
            ]
        )
    return ModelRequest(provider=provider, model="test-model", messages=messages)


def test_message_rejects_private_state_and_calls_on_user_role():
    """HTTP-compatible models cannot submit private continuation or disguise calls as users."""
    with pytest.raises(ValueError):
        Message(
            role="user",
            content="hello",
            tool_calls=[ToolCall(id="1", name="x", arguments={})],
        )
    with pytest.raises(ValueError):
        Message(
            role="assistant", content="hi", _provider_continuation_state={"secret": "x"}
        )
    with pytest.raises(ValueError):
        Message(role="assistant", content="")


def test_openai_history_pairs_each_function_call_and_result_in_order():
    """Responses input carries assistant function calls rather than orphaned results."""
    provider = OpenAIResponsesProvider(
        None, base_url="https://example.invalid", api_key="test"
    )
    payload = provider._build_payload(tool_history(ProviderName.OPENAI), stream=False)
    assert [item.get("type") for item in payload["input"]] == [
        None,
        "function_call",
        "function_call_output",
        "function_call",
        "function_call_output",
    ]
    assert payload["input"][1]["call_id"] == payload["input"][2]["call_id"] == "call-0"


def test_deepseek_replays_each_turn_reasoning_without_duplicate_assistant_calls():
    """Two DeepSeek tool rounds retain their own private reasoning instead of only the last one."""
    provider = DeepSeekChatProvider(
        None, base_url="https://example.invalid", api_key="test"
    )
    request = tool_history(ProviderName.DEEPSEEK)
    for index, message in enumerate([request.messages[1], request.messages[3]]):
        message._provider_continuation_state = ProviderContinuationState(
            kind=ProviderContinuationKind.DEEPSEEK_RAW_REASONING,
            raw_reasoning_for_tool_continuation=f"private-round-{index}",
        )
    wire = provider._build_payload(request, stream=False)["messages"]
    assert len(wire) == 5
    assert wire[1]["reasoning_content"] == "private-round-0"
    assert wire[3]["reasoning_content"] == "private-round-1"
    assert "private-round" not in request.model_dump_json()


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("rounds", [1, 2])
async def test_deepseek_message_states_pass_public_request_validation(stream, rounds):
    """Send one/two tool rounds through generate/stream, preserving each private state on wire."""
    request = tool_history(ProviderName.DEEPSEEK)
    request.messages = request.messages[: 1 + 2 * rounds]
    for index, message in enumerate(request.messages[1::2]):
        message._provider_continuation_state = ProviderContinuationState(
            kind=ProviderContinuationKind.DEEPSEEK_RAW_REASONING,
            raw_reasoning_for_tool_continuation=f"private-round-{index}",
        )
    sent = []

    async def handler(http_request):
        """Inspect the actual serialized request and return a final JSON or SSE answer."""
        payload = json.loads(http_request.content)
        sent.append(payload)
        messages = payload["messages"]
        assert len(messages) == 1 + 2 * rounds
        for index in range(rounds):
            assistant, tool = messages[1 + 2 * index : 3 + 2 * index]
            assert assistant["reasoning_content"] == f"private-round-{index}"
            assert (
                assistant["tool_calls"][0]["id"]
                == tool["tool_call_id"]
                == f"call-{index}"
            )
        if stream:
            return httpx.Response(
                200,
                headers={"Content-Type": "text/event-stream"},
                text=(
                    'data: {"id":"final","choices":[{"delta":{"content":"Read complete"},'
                    '"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n'
                ),
            )
        return httpx.Response(
            200,
            json={
                "id": "final",
                "choices": [
                    {"message": {"content": "Read complete"}, "finish_reason": "stop"}
                ],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = DeepSeekChatProvider(
            HttpTransport(client, max_retries=0),
            base_url="https://example.invalid",
            api_key="test",
        )
        if stream:
            events = [event async for event in provider.stream(request)]
            assert events[-1].type is StreamEventType.COMPLETED
            assert events[-1].data["response"]["text"] == "Read complete"
        else:
            assert (await provider.generate(request)).text == "Read complete"
    assert len(sent) == 1
    assert request.provider_continuation_state is None
    assert "private-round" not in request.model_dump_json()


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("invalid_round", [0, 1])
@pytest.mark.parametrize("state_kind", ["missing", "wrong_provider"])
async def test_deepseek_rejects_invalid_message_state_even_with_legacy_state(
    stream,
    invalid_round,
    state_kind,
):
    """A valid top-level state cannot hide a missing/wrong state on any assistant tool round."""
    request = tool_history(ProviderName.DEEPSEEK)
    valid = ProviderContinuationState(
        kind=ProviderContinuationKind.DEEPSEEK_RAW_REASONING,
        raw_reasoning_for_tool_continuation="private-valid",
    )
    request.provider_continuation_state = valid
    for message in request.messages[1::2]:
        message._provider_continuation_state = valid
    request.messages[1 + invalid_round * 2]._provider_continuation_state = (
        None
        if state_kind == "missing"
        else ProviderContinuationState(
            kind=ProviderContinuationKind.OPENAI_RESPONSE_ID,
            provider_response_id="other-provider",
        )
    )
    sent = []

    async def handler(http_request):
        """Record any accidental outbound request; invalid state must fail before transport."""
        sent.append(http_request)
        return httpx.Response(500)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = DeepSeekChatProvider(
            HttpTransport(client, max_retries=0),
            base_url="https://example.invalid",
            api_key="test",
        )
        with pytest.raises(ModelProviderError, match="continuation"):
            if stream:
                _ = [event async for event in provider.stream(request)]
            else:
                await provider.generate(request)
    assert sent == []


@pytest.mark.parametrize("provider_name", [ProviderName.ANTHROPIC, ProviderName.GEMINI])
def test_signed_tool_turns_round_trip_without_changing_blocks(provider_name):
    """Signed thinking/tool parts are retained unchanged and excluded from public serialization."""
    request = tool_history(provider_name)
    if provider_name == ProviderName.ANTHROPIC:
        provider = AnthropicMessagesProvider(
            None, base_url="https://example.invalid", api_key="test"
        )
        blocks = [
            {
                "type": "thinking",
                "thinking": "private-thought",
                "signature": "signed-1",
            },
            {
                "type": "tool_use",
                "id": "call-0",
                "name": "read_file_content",
                "input": {"offset": 0},
            },
        ]
        raw = {"content": blocks, "stop_reason": "tool_use"}
    else:
        provider = GeminiGenerateContentProvider(
            None, base_url="https://example.invalid", api_key="test"
        )
        blocks = [
            {"text": "private-thought", "thought": True},
            {
                "functionCall": {
                    "id": "call-0",
                    "name": "read_file_content",
                    "args": {"offset": 0},
                },
                "thoughtSignature": "signed-1",
            },
        ]
        raw = {"candidates": [{"content": {"parts": blocks}, "finishReason": "STOP"}]}
    response = provider._parse_response(raw, request, latency_ms=1, attempts=[])
    assert "private-thought" not in (response.text or "")
    request.messages[
        1
    ]._provider_continuation_state = response.provider_continuation_state
    if provider_name == ProviderName.ANTHROPIC:
        wire_blocks = provider._build_payload(request, stream=False)["messages"][1][
            "content"
        ]
    else:
        wire_blocks = provider._build_payload(request)["contents"][1]["parts"]
    assert wire_blocks == blocks
    assert "signed-1" not in json.dumps(request.model_dump())
