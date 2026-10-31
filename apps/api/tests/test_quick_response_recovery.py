"""Exercise quick-turn execution ownership and reuse without paid providers."""

from datetime import timedelta

import pytest
from sqlalchemy import update
from test_api import create_test_run

from nexuspilot_api.core.errors import ResourceConflictError
from nexuspilot_api.models.base import utc_now
from nexuspilot_api.models.quick_response import QuickResponseExecution
from nexuspilot_api.services.quick_response_ownership import claim_response, verify_response_claim


@pytest.mark.parametrize("boundary", ["tool", "final", "unknown_model"])
async def test_quick_crash_reuses_completed_calls(
    client, test_database_session_factory, monkeypatch, boundary
):
    """Resume committed tool/model boundaries without repeating any completed provider call."""
    import json

    from conftest import fake_price_catalog
    from sqlalchemy import func, select
    from test_agent_workflows import scripted_registry
    from test_conversation_file_context import prepare_conversation_file
    from test_response_file_tools import QuickFileProvider

    from nexuspilot_api.models import LlmMessage, LlmModelAttempt, LlmModelToolCall
    from nexuspilot_api.schemas.responses import ResponsesRequest
    from nexuspilot_api.services.agent_file_tool_service import AgentFileToolService
    from nexuspilot_api.services.quick_response_submission import accept_quick_response
    from nexuspilot_api.services.quick_response_worker import execute_quick_response

    class ConsumerCrash(BaseException):
        """Interrupt outside normal failure handling to represent process loss."""

    owner, _, _, turn = await prepare_conversation_file(client)
    provider = QuickFileProvider()
    if boundary == "unknown_model":

        async def lose_provider_response(request):
            """Leave a dispatched attempt without a committed provider result."""
            provider.requests.append(request)
            raise ConsumerCrash()

        monkeypatch.setattr(provider, "generate", lose_provider_response)
    async with test_database_session_factory() as db:
        storage = db.body_storage
        accepted = await accept_quick_response(
            db,
            storage,
            owner,
            ResponsesRequest(
                run_id=turn["run"]["run_id"],
                current_user_message_id=turn["message"]["message_id"],
                provider="openai",
                model="test-model",
                input="Read attachment",
                timeout_seconds=None,
                idempotency_key="crash-boundary-test",
                stream=True,
            ),
        )
        identity = accepted.response_execution_id
    execute = AgentFileToolService.execute
    put = storage.put_bytes
    crashed = False

    async def crash_after_tool(self, **kwargs):
        """Persist the first tool result then lose the consumer before it updates history."""
        nonlocal crashed
        result = await execute(self, **kwargs)
        if boundary == "tool" and not crashed:
            crashed = True
            raise ConsumerCrash()
        return result

    async def crash_before_final_event(name, body, content_type):
        """Lose the final transaction after the provider has already committed its result."""
        nonlocal crashed
        if (
            boundary == "final"
            and "/events/" in name
            and not crashed
            and json.loads(body).get("type") == "response.completed"
        ):
            crashed = True
            raise ConsumerCrash()
        return await put(name, body, content_type)

    monkeypatch.setattr(AgentFileToolService, "execute", crash_after_tool)
    monkeypatch.setattr(storage, "put_bytes", crash_before_final_event)
    registry = scripted_registry(provider)
    with pytest.raises(ConsumerCrash):
        await execute_quick_response(
            test_database_session_factory, storage, registry, fake_price_catalog(), identity
        )
    async with test_database_session_factory() as db:
        await db.execute(
            update(QuickResponseExecution)
            .where(QuickResponseExecution.response_execution_id == identity)
            .values(lease_expires_at=utc_now() - timedelta(seconds=1))
        )
        await db.commit()
    await execute_quick_response(
        test_database_session_factory, storage, registry, fake_price_catalog(), identity
    )
    async with test_database_session_factory() as db:
        record = await db.get(QuickResponseExecution, identity)
        if boundary == "unknown_model":
            assert record.status == "outcome_unknown"
            assert record.assistant_message_id is None
            assert len(provider.requests) == 1
            return
        assert record.status == "completed"
        assert record.assistant_message_id
        assert (
            await db.scalar(
                select(func.count())
                .select_from(LlmModelAttempt)
                .where(LlmModelAttempt.run_id == turn["run"]["run_id"])
            )
            == 3
        )
        assert (
            await db.scalar(
                select(func.count())
                .select_from(LlmModelToolCall)
                .join(LlmModelAttempt, LlmModelToolCall.attempt_id == LlmModelAttempt.attempt_id)
                .where(LlmModelAttempt.run_id == turn["run"]["run_id"])
            )
            == 2
        )
        assert (
            await db.scalar(
                select(func.count())
                .select_from(LlmMessage)
                .where(LlmMessage.run_id == turn["run"]["run_id"], LlmMessage.role == "ASSISTANT")
            )
            == 1
        )
    assert len(provider.requests) == 3


async def test_expired_response_claim_fences_old_executor(client, test_database_session_factory):
    """Only an expired lease can be replaced; the previous consumer may no longer commit."""
    run = await create_test_run(client)
    async with test_database_session_factory() as db:
        from nexuspilot_api.models import LlmRun

        owner = (await db.get(LlmRun, run["run_id"])).user_id
        row = QuickResponseExecution(
            run_id=run["run_id"],
            owner_user_id=owner,
            request_key="claim-recovery",
            request_uri="memory://request",
        )
        db.add(row)
        await db.commit()
        identity = row.response_execution_id
    first = await claim_response(test_database_session_factory, identity, "worker-one")
    assert first is not None
    assert await claim_response(test_database_session_factory, identity, "worker-two") is None
    async with test_database_session_factory() as db:
        await db.execute(
            update(QuickResponseExecution)
            .where(QuickResponseExecution.response_execution_id == identity)
            .values(lease_expires_at=utc_now() - timedelta(seconds=1))
        )
        await db.commit()
    second = await claim_response(test_database_session_factory, identity, "worker-two")
    assert second.version == first.version + 1
    async with test_database_session_factory() as db:
        db.info["quick_response_claim"] = first
        with pytest.raises(ResourceConflictError):
            await verify_response_claim(db)
        db.info["quick_response_claim"] = second
        await verify_response_claim(db)
