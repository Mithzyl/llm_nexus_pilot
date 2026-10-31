"""Accept whole-turn requests with owner-scoped keys and exact saved-parameter comparison."""

import asyncio
import json

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError

from nexuspilot_api.core.errors import (
    InvalidRequestError,
    ResourceConflictError,
    ResourceNotFoundError,
)
from nexuspilot_api.models import LlmAgentWorkflowExecution, LlmMessage, LlmRun
from nexuspilot_api.models.base import new_id
from nexuspilot_api.models.enums import MessageRole, RunStatus
from nexuspilot_api.models.quick_response import QuickResponseExecution


async def read_request(storage, uri):
    """Read the saved JSON request; storage failures propagate rather than imply no execution."""
    obj = await storage.open_object(uri)
    return json.loads(await asyncio.to_thread(b"".join, obj.chunks))


async def accept_quick_response(db, storage, owner, payload):
    """Lock the owned Run and persist one request; never change identity on retries.

    HTTP streaming preference is transport-only. All model and context parameters
    must compare equal. A duplicate with unknown execution remains that execution.
    """
    if not owner or not payload.idempotency_key or not payload.current_user_message_id:
        raise InvalidRequestError("A quick response requires owner, message and request key")
    if payload.task_id or payload.tools:
        raise InvalidRequestError(
            "Task and explicit-tool requests are not conversation submissions"
        )
    run = await db.scalar(
        select(LlmRun)
        .where(LlmRun.run_id == payload.run_id, LlmRun.user_id == owner)
        .with_for_update()
    )
    if run is None:
        raise ResourceNotFoundError("Run")
    request = payload.model_dump(mode="json", exclude={"stream"})
    existing = await db.scalar(
        select(QuickResponseExecution).where(
            or_(
                QuickResponseExecution.run_id == payload.run_id,
                (QuickResponseExecution.owner_user_id == owner)
                & (QuickResponseExecution.request_key == payload.idempotency_key),
            )
        )
    )
    if existing:
        if (
            existing.owner_user_id != owner
            or await read_request(storage, existing.request_uri) != request
        ):
            raise ResourceConflictError("Quick response request key or parameters conflict")
        await db.commit()
        return existing
    message = await db.scalar(
        select(LlmMessage.message_id).where(
            LlmMessage.message_id == payload.current_user_message_id,
            LlmMessage.run_id == run.run_id,
            LlmMessage.session_id == run.session_id,
            LlmMessage.role == MessageRole.USER,
        )
    )
    if not message:
        raise InvalidRequestError("Conversation message does not belong to this Run")
    workflow = await db.scalar(
        select(LlmAgentWorkflowExecution.workflow_execution_id).where(
            LlmAgentWorkflowExecution.run_id == run.run_id
        )
    )
    if run.status != RunStatus.PENDING or workflow:
        raise ResourceConflictError("Run is already executing, ended, or owned by a workflow")
    execution_id = new_id()
    obj = await storage.put_bytes(
        f"quick-responses/{owner}/{execution_id}/request.json",
        json.dumps(request, ensure_ascii=False, allow_nan=False).encode(),
        "application/json",
    )
    row = QuickResponseExecution(
        response_execution_id=execution_id,
        run_id=run.run_id,
        owner_user_id=owner,
        request_key=payload.idempotency_key,
        request_uri=obj.uri,
    )
    db.add(row)
    try:
        await db.commit()
    except IntegrityError:
        # A concurrent key claim in another Run must never trigger another model call.
        await db.rollback()
        raise ResourceConflictError(
            "Quick response was concurrently submitted; retry the same request"
        ) from None
    return row
