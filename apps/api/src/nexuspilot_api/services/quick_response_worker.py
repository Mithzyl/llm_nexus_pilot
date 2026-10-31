"""Consume queued quick turns independently of HTTP observers; do not replay unknown calls."""

import asyncio
import uuid

from nexuspilot_models.contracts import StreamEventType
from sqlalchemy import func, select

from nexuspilot_api.models import LlmRun
from nexuspilot_api.models.base import utc_now
from nexuspilot_api.models.enums import RunStatus
from nexuspilot_api.models.quick_response import QuickResponseEvent, QuickResponseExecution
from nexuspilot_api.schemas.responses import ResponsesRequest
from nexuspilot_api.schemas.sessions import MessageCreate
from nexuspilot_api.services.model_response_service import ModelInvocationService
from nexuspilot_api.services.quick_response_ownership import claim_response, renew_response_claim
from nexuspilot_api.services.quick_response_recovery import QuickResponseOutcomeUnknown
from nexuspilot_api.services.quick_response_submission import read_request
from nexuspilot_api.services.response_run_lifecycle import ConversationResponseService
from nexuspilot_api.services.session_service import create_message
from nexuspilot_api.services.user_interactions import InteractionPending


async def finish_interrupted_response(db, execution_id, *, cancelled):
    """Release a prepared/running Run without overwriting explicit cancellation or success."""
    await db.rollback()
    record = await db.get(QuickResponseExecution, execution_id)
    run = await db.scalar(
        select(LlmRun)
        .where(LlmRun.run_id == record.run_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    explicit = run.status in {RunStatus.CANCEL_REQUESTED, RunStatus.CANCELLED}
    # A consumer shutdown is not proof that the provider stopped or never returned.
    record.status = "cancelled" if explicit else "outcome_unknown" if cancelled else "failed"
    if run.status in {RunStatus.PENDING, RunStatus.RUNNING, RunStatus.CANCEL_REQUESTED}:
        run.status = RunStatus.CANCELLED if explicit else RunStatus.FAILED
        run.completed_at = utc_now()
    await db.commit()


async def execute_quick_response(factory, storage, registry, prices, execution_id):
    """Own an expiring lease, heartbeat during provider waits and fence superseded consumers."""
    claim = await claim_response(factory, execution_id, "quick-" + str(uuid.uuid4()))
    if claim is None:
        return False
    task = asyncio.create_task(_execute_claim(factory, storage, registry, prices, claim))

    async def heartbeat():
        """Cancel local execution if renewal fails; stale writes are also rejected by SQL checks."""
        try:
            while not task.done():
                await asyncio.sleep(10)
                if task.done():
                    return
                if not await renew_response_claim(factory, claim):
                    task.cancel()
                    return
        except Exception:
            task.cancel()
            raise

    monitor = asyncio.create_task(heartbeat())
    try:
        return await task
    finally:
        monitor.cancel()
        await asyncio.gather(monitor, return_exceptions=True)


async def _execute_claim(factory, storage, registry, prices, claim):
    """Rebuild completed model/tool boundaries; unknown outcomes never authorize redispatch.

    The final reply and event are committed atomically with terminal Run state.
    """
    execution_id = claim.execution_id
    async with factory() as db:
        db.info["quick_response_claim"] = claim
        row = await db.get(QuickResponseExecution, execution_id)
        db.info["authorized_user_id"] = row.owner_user_id
        db.info["body_storage"] = storage
        try:
            payload = ResponsesRequest.model_validate(await read_request(storage, row.request_uri))
            payload = payload.model_copy(update={"stream": True})
            invocation = ModelInvocationService(
                registry=registry, prices=prices, db_session=db, storage=storage
            )
            lifecycle = ConversationResponseService(invocation)
            lifecycle.defer_finalization = True
            sequence = (
                await db.scalar(
                    select(func.max(QuickResponseEvent.sequence)).where(
                        QuickResponseEvent.response_execution_id == execution_id
                    )
                )
                or 0
            )
            async for event in lifecycle.stream(payload):
                sequence += 1
                event = event.model_copy(update={"sequence": sequence})
                # Run cancellation is authoritative even when a provider returns late.
                run = await db.scalar(
                    select(LlmRun)
                    .where(LlmRun.run_id == row.run_id)
                    .execution_options(populate_existing=True)
                    .with_for_update()
                )
                if run.status in {RunStatus.CANCELLED, RunStatus.CANCEL_REQUESTED}:
                    raise asyncio.CancelledError()
                if event.type == StreamEventType.COMPLETED:
                    result = event.data.get("response") or {}
                    text = result.get("output_text")
                    if text:
                        run = await db.get(LlmRun, row.run_id)
                        message = await create_message(
                            db,
                            run.session_id,
                            MessageCreate(
                                role="assistant",
                                content_text=text,
                                run_id=run.run_id,
                                source_model_attempt_id=result.get("id"),
                            ),
                            commit=False,
                        )
                        await db.flush()
                        row.assistant_message_id = message.message_id
                        event = event.model_copy(
                            update={
                                "data": {**event.data, "assistant_message_id": message.message_id}
                            }
                        )
                    row.status = "completed"
                    run.status, run.completed_at = RunStatus.COMPLETED, utc_now()
                elif event.type == StreamEventType.FAILED:
                    row.status = "failed"
                    run.status, run.completed_at = RunStatus.FAILED, utc_now()
                obj = await storage.put_bytes(
                    f"quick-responses/{row.owner_user_id}/{execution_id}/events/{sequence}.json",
                    event.model_dump_json().encode(),
                    "application/json",
                )
                db.add(
                    QuickResponseEvent(
                        response_execution_id=execution_id, sequence=sequence, body_uri=obj.uri
                    )
                )
                await db.commit()
            if row.status == "running":
                await finish_interrupted_response(db, execution_id, cancelled=False)
            return True
        except InteractionPending:
            return True
        except asyncio.CancelledError:
            await finish_interrupted_response(db, execution_id, cancelled=True)
            if asyncio.current_task().cancelling():
                raise
            return True
        except QuickResponseOutcomeUnknown:
            await finish_interrupted_response(db, execution_id, cancelled=True)
            return True
        except Exception:
            await finish_interrupted_response(db, execution_id, cancelled=False)
            raise
