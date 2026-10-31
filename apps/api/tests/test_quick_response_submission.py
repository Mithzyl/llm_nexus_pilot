"""Check durable whole-turn submission without invoking a model or external service."""

import pytest
from sqlalchemy import select
from test_api import create_test_run

from nexuspilot_api.core.errors import ResourceConflictError, ResourceNotFoundError
from nexuspilot_api.models import LlmMessage, LlmRun
from nexuspilot_api.models.enums import MessageRole
from nexuspilot_api.schemas.responses import ResponsesRequest
from nexuspilot_api.services.quick_response_submission import accept_quick_response


@pytest.mark.parametrize("cancelled", [False, True])
async def test_quick_worker_saves_reply_once_and_rejects_late_cancelled_result(
    client,
    test_database_session_factory,
    monkeypatch,
    cancelled,
):
    """A fixed response saves on the server once; a cancelled Run never receives late text."""
    from nexuspilot_models.contracts import StreamEvent, StreamEventType

    from nexuspilot_api.models.enums import RunStatus
    from nexuspilot_api.models.quick_response import QuickResponseExecution
    from nexuspilot_api.services import quick_response_worker as worker

    calls = []

    class FixedLifecycle:
        """Return a deterministic terminal event without contacting a provider."""

        def __init__(self, invocation):
            """Retain the worker database to simulate a committed terminal Run."""
            self.db = invocation.db_session

        async def stream(self, payload):
            """Simulate one invocation and optionally a cancellation racing its result."""
            calls.append(payload.run_id)
            row = await self.db.get(LlmRun, payload.run_id)
            row.status = RunStatus.CANCELLED if cancelled else RunStatus.COMPLETED
            await self.db.commit()
            yield StreamEvent(
                type=StreamEventType.COMPLETED,
                sequence=1,
                data={"response": {"output_text": "fixed answer"}},
            )

    monkeypatch.setattr(worker, "ConversationResponseService", FixedLifecycle)
    run = await create_test_run(client)
    async with test_database_session_factory() as db:
        row = await db.get(LlmRun, run["run_id"])
        from nexuspilot_api.schemas.sessions import MessageCreate
        from nexuspilot_api.services.session_service import create_message

        message = await create_message(
            db, row.session_id, MessageCreate(role="user", run_id=row.run_id, content_text="hello")
        )
        payload = ResponsesRequest(
            run_id=row.run_id,
            current_user_message_id=message.message_id,
            input="hello",
            provider="openai",
            model="fixture",
            idempotency_key="worker-quick-key",
        )
        accepted = await accept_quick_response(db, db.body_storage, row.user_id, payload)
        execution_id, storage = accepted.response_execution_id, db.body_storage
    assert await worker.execute_quick_response(
        test_database_session_factory, storage, None, None, execution_id
    )
    assert not await worker.execute_quick_response(
        test_database_session_factory, storage, None, None, execution_id
    )
    async with test_database_session_factory() as db:
        record = await db.get(QuickResponseExecution, execution_id)
        assert record.status == ("cancelled" if cancelled else "completed")
        assert bool(record.assistant_message_id) is not cancelled
    assert len(calls) == 1
    from nexuspilot_api.services.quick_response_observer import observe_quick_response

    events = [
        event
        async for event in observe_quick_response(
            test_database_session_factory, storage, accepted.owner_user_id, execution_id
        )
    ]
    assert events[-1].type == (StreamEventType.FAILED if cancelled else StreamEventType.COMPLETED)
    with pytest.raises(ResourceNotFoundError):
        await anext(
            observe_quick_response(
                test_database_session_factory, storage, "other-owner", execution_id
            )
        )


async def test_quick_submission_reuses_identity_and_rejects_changed_parameters(
    client,
    test_database_session_factory,
):
    """One Run gets one immutable request; owner and parameter changes cannot reuse it."""
    run = await create_test_run(client)
    async with test_database_session_factory() as db:
        fake_object_storage = db.body_storage
        stored = await db.get(LlmRun, run["run_id"])
        owner = stored.user_id
        message = LlmMessage(
            session_id=stored.session_id,
            run_id=stored.run_id,
            role=MessageRole.USER,
            sequence=1,
            content_text="hello",
        )
        db.add(message)
        await db.commit()
        payload = ResponsesRequest(
            run_id=stored.run_id,
            current_user_message_id=message.message_id,
            input="hello",
            provider="openai",
            model="fixture",
            stream=True,
            idempotency_key="quick-stable-key",
        )
        first = await accept_quick_response(db, fake_object_storage, owner, payload)
        second = await accept_quick_response(db, fake_object_storage, owner, payload)
        assert first.response_execution_id == second.response_execution_id
        assert first.status == "queued"
        for changes in ({"model": "different"}, {"idempotency_key": "another-stable-key"}):
            with pytest.raises(ResourceConflictError):
                await accept_quick_response(
                    db, fake_object_storage, owner, payload.model_copy(update=changes)
                )
        with pytest.raises(ResourceNotFoundError):
            await accept_quick_response(db, fake_object_storage, "other-owner", payload)
        assert (
            await db.scalar(select(LlmRun.status).where(LlmRun.run_id == stored.run_id))
        ).value == "pending"
