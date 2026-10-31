"""Observe persisted quick-turn events without owning or cancelling their execution."""

import asyncio

from nexuspilot_models.contracts import StreamEvent, StreamEventType
from sqlalchemy import select

from nexuspilot_api.core.errors import ResourceNotFoundError
from nexuspilot_api.models.quick_response import QuickResponseEvent, QuickResponseExecution
from nexuspilot_api.services.quick_response_submission import read_request


async def observe_quick_response(factory, storage, owner, execution_id, *, after_sequence=0):
    """Replay committed events in order, then observe new ones in short read transactions.

    A disconnected observer has no execution reference to cancel. Ownership is
    checked on every read before an event body is opened.
    """
    cursor = after_sequence
    while True:
        async with factory() as db:
            row = await db.scalar(
                select(QuickResponseExecution).where(
                    QuickResponseExecution.response_execution_id == execution_id,
                    QuickResponseExecution.owner_user_id == owner,
                )
            )
            if row is None:
                raise ResourceNotFoundError("Response")
            status = row.status
            events = list(
                await db.scalars(
                    select(QuickResponseEvent)
                    .where(
                        QuickResponseEvent.response_execution_id == execution_id,
                        QuickResponseEvent.sequence > cursor,
                    )
                    .order_by(QuickResponseEvent.sequence)
                    .limit(100)
                )
            )
        for item in events:
            event = StreamEvent.model_validate(await read_request(storage, item.body_uri))
            cursor = item.sequence
            yield event
            if event.type in {StreamEventType.COMPLETED, StreamEventType.FAILED}:
                return
        if len(events) == 100:
            continue
        if status not in {"queued", "running", "waiting_for_input"}:
            yield StreamEvent(
                type=StreamEventType.FAILED,
                sequence=cursor + 1,
                data={"error": {"code": status, "message": "本次执行已结束，请查看运行状态。"}},
            )
            return
        await asyncio.sleep(0.25)
