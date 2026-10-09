"""Check complete multi-round tool wire formats without external Provider calls."""

import json

import pytest
from nexuspilot_models.contracts import (
    Message,
    MessageRole,
    ModelRequest,
    ProviderContinuationKind,
    ProviderContinuationState,
    ProviderName,
    ToolCall,
)
from nexuspilot_models.providers.anthropic import AnthropicMessagesProvider
from nexuspilot_models.providers.deepseek import DeepSeekChatProvider
from nexuspilot_models.providers.gemini import GeminiGenerateContentProvider
from nexuspilot_models.providers.openai import OpenAIResponsesProvider


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
