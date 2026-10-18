"""Check whole-turn state transitions without making external model requests."""

import asyncio
from types import SimpleNamespace

import pytest
from nexuspilot_models.contracts import StreamEvent, StreamEventType
from sqlalchemy import select
from test_api import create_test_run

from nexuspilot_api.core.errors import InvalidRequestError, ResourceConflictError
from nexuspilot_api.models import LlmMessage, LlmRun
from nexuspilot_api.models.enums import MessageRole
from nexuspilot_api.routers.responses import post_response
from nexuspilot_api.schemas.responses import ResponsesRequest
from nexuspilot_api.services.response_run_lifecycle import ConversationResponseService


@pytest.mark.parametrize("outcome", ["completed", "failed", "cancelled", "exception", "incomplete"])
@pytest.mark.parametrize("streaming", [True, False])
async def test_quick_response_run_terminal_state(
    client, test_database_session_factory, outcome, streaming
):
    """Finalize the entire SSE response, including cancellation and premature termination."""
    run = await create_test_run(client)
    async with test_database_session_factory() as db:
        message = LlmMessage(
            session_id=run["session_id"],
            run_id=run["run_id"],
            role=MessageRole.USER,
            sequence=1,
            content_text="test",
        )
        db.add(message)
        await db.commit()
        payload = ResponsesRequest(
            run_id=run["run_id"],
            current_user_message_id=message.message_id,
            provider="openai",
            model="fixture",
            input="test",
            stream=streaming,
        )

        async def stream(_payload):
            """Simulate the facade event protocol, never a network provider."""
            if outcome == "cancelled":
                raise asyncio.CancelledError()
            if outcome == "exception":
                raise RuntimeError("fixture failure")
            if outcome != "incomplete":
                yield StreamEvent(
                    type=StreamEventType.COMPLETED
                    if outcome == "completed"
                    else StreamEventType.FAILED,
                    sequence=1,
                    data={},
                )

        async def generate(_payload):
            """Match nonstreaming facade failures without a provider dependency."""
            if outcome == "cancelled":
                raise asyncio.CancelledError()
            if outcome != "completed":
                raise RuntimeError("fixture failure")
            return SimpleNamespace(output_text="fixture answer")

        service = SimpleNamespace(db_session=db, stream=stream, generate=generate)
        try:
            response = await post_response(payload, service)
            if streaming:
                async for _ in response.body_iterator:
                    pass
        except (asyncio.CancelledError, RuntimeError):
            pass
        await db.refresh(await db.get(LlmRun, run["run_id"]))
        stored = await db.scalar(select(LlmRun).where(LlmRun.run_id == run["run_id"]))
        assert stored.status.value == (
            outcome if outcome in {"completed", "cancelled"} else "failed"
        )
        assert stored.completed_at is not None


async def test_chat_run_claim_rejects_duplicate_and_unrelated_message(
    client, test_database_session_factory
):
    """Conditional updates prevent duplicate dispatch and never mutate an unrelated Run."""
    run = await create_test_run(client)
    async with test_database_session_factory() as db:
        payload = ResponsesRequest(
            run_id=run["run_id"],
            current_user_message_id="missing",
            provider="openai",
            model="fixture",
            input="test",
        )
        lifecycle = ConversationResponseService(SimpleNamespace(db_session=db))
        with pytest.raises(InvalidRequestError):
            await lifecycle._start(payload)
        stored = await db.get(LlmRun, run["run_id"])
        assert stored.status.value == "pending"
        message = LlmMessage(
            session_id=run["session_id"],
            run_id=run["run_id"],
            role=MessageRole.USER,
            sequence=1,
            content_text="test",
        )
        db.add(message)
        await db.commit()
        payload.current_user_message_id = message.message_id
        await lifecycle._start(payload)
        with pytest.raises(ResourceConflictError):
            await ConversationResponseService(SimpleNamespace(db_session=db))._start(payload)
