"""Persist in-flight stream drafts privately without sharing the caller's SQL transaction."""

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import datetime

from anyio import CancelScope
from nexuspilot_models.errors import ModelProviderError
from nexuspilot_models.stream_progress import ModelStreamProgress
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from nexuspilot_api.models import AttemptStatus, LlmModelAttempt


async def save_inflight_progress(sessions, storage, *, attempt_id, progress):
    """Write one rolling private object and link it only while its Attempt is STARTED.

    The stable per-attempt object avoids accumulating unreferenced batches. Final
    responses use a different object, so a late PUT cannot overwrite a final answer.
    This is not a checkpoint authorizing tools or model continuation.
    """
    async with sessions() as db:
        row = (await db.execute(select(
            LlmModelAttempt.run_id, LlmModelAttempt.status,
        ).where(LlmModelAttempt.attempt_id == attempt_id))).one_or_none()
    if row is None or row.status != AttemptStatus.STARTED:
        return
    stored = await storage.put_bytes(
        f"{row.run_id}/{attempt_id}/stream-progress.json",
        json.dumps({
            "record_kind": "inflight_stream",
            "usage_complete": False,
            "stream_progress": progress,
        }, ensure_ascii=False).encode(),
        "application/json",
    )
    values = {"raw_response_uri": stored.uri}
    if progress.get("first_text_at"):
        values["first_visible_token_at"] = datetime.fromisoformat(progress["first_text_at"])
    async with sessions() as db:
        await db.execute(update(LlmModelAttempt).where(
            LlmModelAttempt.attempt_id == attempt_id,
            LlmModelAttempt.status == AttemptStatus.STARTED,
        ).values(**values))
        await db.commit()


@asynccontextmanager
async def persist_stream_progress(db, storage, request, *, attempt_id, interval_seconds=2.0):
    """Flush changed progress every two seconds, including while the provider is idle.

    Writes have a five-second local deadline and use an independent session. The
    maximum scheduled unsaved window is the interval plus write latency, not a
    guarantee under process failure or unavailable storage. A failed write stops
    further execution instead of pretending the draft is durable.
    """
    progress = ModelStreamProgress()
    previous = request._stream_progress_capture
    request._stream_progress_capture = progress
    sessions = async_sessionmaker(db.bind, expire_on_commit=False)
    owner = asyncio.current_task()
    failure = None
    induced_cancel_count = None

    async def flush_batches():
        """Persist each changed snapshot once; interrupt the owner on audit failure."""
        nonlocal failure, induced_cancel_count
        saved_count = 0
        try:
            while True:
                await asyncio.sleep(interval_seconds)
                if progress.completed:
                    return
                if progress.event_count == saved_count:
                    continue
                snapshot = progress.snapshot()
                async with asyncio.timeout(5):
                    await save_inflight_progress(
                        sessions, storage, attempt_id=attempt_id, progress=snapshot,
                    )
                saved_count = snapshot["event_count"]
        except asyncio.CancelledError:
            raise
        except Exception as error:
            failure = error
            if not owner.cancelling():
                owner.cancel()
                induced_cancel_count = owner.cancelling()

    writer = asyncio.create_task(flush_batches(), name=f"stream-progress:{attempt_id}")
    try:
        yield progress
    finally:
        # ASGI cancellation must not leave a detached writer updating the Attempt.
        with CancelScope(shield=True):
            writer.cancel()
            await asyncio.gather(writer, return_exceptions=True)
            request._stream_progress_capture = previous
        if failure is not None and induced_cancel_count == owner.cancelling():
            # Remove only our own cancellation. A simultaneous user stop remains
            # cancellation and must not be replaced by an ordinary retryable error.
            owner.uncancel()
            error = ModelProviderError(
                "stream_progress_persistence_error",
                "Received model progress could not be saved.",
            )
            error.stream_progress = progress.snapshot()
            raise error from failure
