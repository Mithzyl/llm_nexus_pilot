"""Regression test for effective_messages storage in ModelInvocationService._start_model_attempt."""

import json

import pytest
from conftest import FakeObjectStorage
from nexuspilot_models.contracts import (
    Message,
    MessageRole,
    ModelRequest,
    ProviderContinuationKind,
    ProviderContinuationState,
    ProviderName,
    ToolCall,
    ToolDefinition,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from test_provider_continuation_store import _create_parent_attempt, _test_settings

from nexuspilot_api.schemas.responses import ResponsesRequest
from nexuspilot_api.services.model_response_service import ModelInvocationService

PRIVATE_SENTINEL = "DO_NOT_STORE_THIS_PRIVATE_STATE"


def _build_request_with_history(tools: list[ToolDefinition]) -> ModelRequest:
    """Build tool history with private continuation state."""
    messages = [
        Message(role=MessageRole.USER, content="Initial query"),
        Message(
            role=MessageRole.ASSISTANT,
            content="",
            tool_calls=[ToolCall(id="call-1", name="read", arguments={"path": "/tmp"})],
        ),
        Message(
            role=MessageRole.TOOL,
            content="File contents here",
            tool_call_id="call-1",
        ),
        Message(role=MessageRole.USER, content="Please correct the path."),
    ]
    messages[1]._provider_continuation_state = ProviderContinuationState(
        kind=ProviderContinuationKind.DEEPSEEK_RAW_REASONING,
        raw_reasoning_for_tool_continuation=PRIVATE_SENTINEL,
    )
    return ModelRequest(
        provider=ProviderName.OPENAI,
        model="test-model",
        messages=messages,
        tools=tools,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "has_history,with_tools",
    [
        (True, False),
        (True, True),
        (False, False),
    ],
)
async def test_start_model_attempt_saves_effective_messages_unconditionally(
    test_database_session_factory: async_sessionmaker[AsyncSession],
    has_history: bool,
    with_tools: bool,
) -> None:
    """Verify effective_messages are saved as raw JSON regardless of history or tools presence."""
    storage = FakeObjectStorage()
    async with test_database_session_factory() as db_session:
        await _create_parent_attempt(db_session)
        if with_tools:
            tools = [
                ToolDefinition(
                    name="read", description="Reads a file", input_schema={"type": "object"}
                )
            ]
        else:
            tools = []
        if has_history:
            request = _build_request_with_history(tools)
        else:
            request = ModelRequest(
                provider=ProviderName.OPENAI,
                model="test-model",
                messages=[Message(role=MessageRole.USER, content="plain")],
            )
        payload = ResponsesRequest(
            run_id="run-1",
            provider=ProviderName.OPENAI,
            model="test-model",
            input="Test input text",
        )
        attempt_id = "new-attempt-id"
        context_build_id = None
        prompt_prefix_fingerprint = None

        service = ModelInvocationService(
            registry=None,
            prices=None,
            db_session=db_session,
            storage=storage,
            settings=_test_settings(),
        )

        attempt = await service._start_model_attempt(
            payload=payload,
            request=request,
            attempt_id=attempt_id,
            context_build_id=context_build_id,
            prompt_prefix_fingerprint=prompt_prefix_fingerprint,
        )

        stored = storage.objects[attempt.raw_request_uri][0]
        saved = json.loads(stored)
        assert saved["effective_messages"] == [m.model_dump(mode="json") for m in request.messages]
        assert saved["input"] == "Test input text"
        assert PRIVATE_SENTINEL.encode() not in stored
