"""Check persisted human replies, ownership and replay without model or network calls."""

import base64

import pytest
from test_api import create_test_run

from nexuspilot_api.core.config import get_settings
from nexuspilot_api.core.errors import ResourceConflictError, ResourceNotFoundError
from nexuspilot_api.models import LlmModelAttempt, LlmModelToolCall, LlmRun
from nexuspilot_api.models.quick_response import QuickResponseExecution
from nexuspilot_api.services.user_interactions import (
    InteractionPending,
    create_interaction,
    list_interactions,
    reply_to_interaction,
)


@pytest.mark.parametrize("kind", ["approval", "input"])
@pytest.mark.parametrize("resolution", ["reply", "cancel", "expire"])
async def test_human_reply_is_owned_versioned_and_replayable(
    client, test_database_session_factory, monkeypatch, kind, resolution
):
    """Only the owner resolves a waiting call; identical replies replay and changed replies conflict."""
    monkeypatch.setattr(
        get_settings(), "memory_encryption_key", base64.urlsafe_b64encode(b"x" * 32).decode()
    )
    from pydantic import SecretStr

    monkeypatch.setattr(
        get_settings(), "memory_encryption_key", SecretStr(get_settings().memory_encryption_key)
    )
    created = await create_test_run(client)
    async with test_database_session_factory() as db:
        run = await db.get(LlmRun, created["run_id"])
        from nexuspilot_api.models import AttemptStatus, RunStatus

        run.status = RunStatus.RUNNING
        attempt = LlmModelAttempt(
            run_id=run.run_id, provider="openai", model="fixed", status=AttemptStatus.COMPLETED
        )
        db.add(attempt)
        await db.flush()
        tool = LlmModelToolCall(
            attempt_id=attempt.attempt_id,
            tool_name="request_user_input",
            tool_version="1.0.0",
            provider_tool_call_id="ask-1",
            input_json={},
            status="running",
            risk_level="read_only",
        )
        execution = QuickResponseExecution(
            run_id=run.run_id,
            owner_user_id=run.user_id,
            request_key="interaction-test",
            request_uri="memory://request",
            status="running",
        )
        db.add_all([tool, execution])
        await db.commit()
        with pytest.raises(InteractionPending):
            await create_interaction(
                db, db.body_storage, attempt, tool, {"kind": kind, "question": "Proceed?"}
            )
        items = await list_interactions(db, db.body_storage, run.user_id, run.run_id)
        assert items[0]["question"] == "Proceed?"
        assert execution.status == "waiting_for_input"
        identity = items[0]["interaction_id"]
        if resolution != "reply":
            from datetime import timedelta

            from nexuspilot_api.models.base import utc_now
            from nexuspilot_api.models.user_interactions import UserInteraction
            from nexuspilot_api.services.run_service import cancel_run
            from nexuspilot_api.services.user_interactions import expire_interactions

            if resolution == "cancel":
                await cancel_run(db, run.run_id)
            else:
                pending = await db.get(UserInteraction, identity)
                pending.expires_at = utc_now() - timedelta(seconds=1)
                await db.commit()
                await expire_interactions(test_database_session_factory)
            pending = await db.get(UserInteraction, identity, populate_existing=True)
            assert pending.status == ("cancelled" if resolution == "cancel" else "expired")
            with pytest.raises(ResourceConflictError):
                await reply_to_interaction(
                    db,
                    db.body_storage,
                    run.user_id,
                    run.run_id,
                    identity,
                    {"expected_version": 1, "key": "reply-key", "approved": True},
                )
            return
        reply = (
            {"expected_version": 1, "key": "reply-key", "approved": True}
            if kind == "approval"
            else {"expected_version": 1, "key": "reply-key", "text": "User answer"}
        )
        with pytest.raises(ResourceNotFoundError):
            await reply_to_interaction(
                db, db.body_storage, "another-owner", run.run_id, identity, reply
            )
        first = await reply_to_interaction(
            db, db.body_storage, run.user_id, run.run_id, identity, reply
        )
        assert first["status"] == "resolved"
        assert (
            await reply_to_interaction(
                db, db.body_storage, run.user_id, run.run_id, identity, reply
            )
            == first
        )
        with pytest.raises(ResourceConflictError):
            await reply_to_interaction(
                db,
                db.body_storage,
                run.user_id,
                run.run_id,
                identity,
                {**reply, "key": "different-key"},
            )
        await db.refresh(tool)
        await db.refresh(execution)
        assert tool.status == "completed"
        assert execution.status == "queued"
