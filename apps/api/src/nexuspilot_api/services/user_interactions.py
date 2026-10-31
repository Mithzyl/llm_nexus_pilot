"""Persist human decisions and resume the same execution without granting extra capabilities."""

import asyncio
import base64
import json
import os
from datetime import UTC, timedelta

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import select

from nexuspilot_api.core.config import get_settings
from nexuspilot_api.core.errors import ResourceConflictError, ResourceNotFoundError
from nexuspilot_api.models import (
    AgentWorkflowStatus,
    LlmAgentWorkflowExecution,
    LlmModelToolCall,
    LlmRun,
    RunStatus,
)
from nexuspilot_api.models.base import new_id, utc_now
from nexuspilot_api.models.quick_response import QuickResponseExecution
from nexuspilot_api.models.user_interactions import UserInteraction


class InteractionPending(BaseException):
    """Suspend a durable consumer without converting human waiting into failure or cancellation."""


def interaction_cipher():
    """Reuse the configured private content key with a distinct interaction-bound AAD."""
    key = get_settings().memory_encryption_key
    if key is None:
        raise ResourceConflictError("Encrypted interaction storage is not configured")
    return AESGCM(base64.urlsafe_b64decode(key.get_secret_value()))


def associated_data(row):
    """Bind encrypted question/reply objects to an immutable owner, Run and interaction."""
    return f"interaction:{row.owner_user_id}:{row.run_id}:{row.interaction_id}".encode()


async def put_body(storage, row, value):
    """Encrypt bounded validated input before publishing its immutable object reference."""
    nonce = os.urandom(12)
    body = json.dumps(value, ensure_ascii=False, allow_nan=False).encode()
    encrypted = nonce + interaction_cipher().encrypt(nonce, body, associated_data(row))
    obj = await storage.put_bytes(
        f"interactions/{row.owner_user_id}/{row.interaction_id}/{new_id()}",
        encrypted,
        "application/octet-stream",
    )
    return obj.uri


async def read_body(storage, row, uri):
    """Decrypt after caller ownership checks; never turn read failure into empty data."""
    obj = await storage.open_object(uri)
    if obj.size_bytes > 32768:
        raise ResourceConflictError("Interaction object is too large")
    data = await asyncio.to_thread(b"".join, obj.chunks)
    return json.loads(interaction_cipher().decrypt(data[:12], data[12:], associated_data(row)))


async def owned_run(db, owner, run_id):
    """Serialize replies against cancellation using the same owned Run lock."""
    run = await db.scalar(
        select(LlmRun)
        .where(LlmRun.run_id == run_id, LlmRun.user_id == owner)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if run is None:
        raise ResourceNotFoundError("Run")
    return run


async def create_interaction(db, storage, attempt, tool, arguments):
    """Publish a waiting tool and suspend its existing consumer in the same transaction."""
    run = await db.get(LlmRun, attempt.run_id)
    run = await owned_run(db, run.user_id, run.run_id)
    if run.status != RunStatus.RUNNING:
        raise ResourceConflictError("Run is no longer running")
    quick = await db.scalar(
        select(QuickResponseExecution).where(QuickResponseExecution.run_id == run.run_id)
    )
    workflow = await db.scalar(
        select(LlmAgentWorkflowExecution).where(LlmAgentWorkflowExecution.run_id == run.run_id)
    )
    if quick is None and (workflow is None or workflow.execution_backend != "worker"):
        raise ResourceConflictError("Human waiting requires durable execution")
    row = UserInteraction(
        interaction_id=new_id(),
        run_id=run.run_id,
        owner_user_id=run.user_id,
        tool_call_id=tool.tool_call_id,
        kind=arguments["kind"],
        expires_at=utc_now() + timedelta(hours=24),
    )
    row.request_uri = await put_body(storage, row, arguments)
    tool.status = "waiting_for_input"
    db.add(row)
    if quick:
        quick.status = "waiting_for_input"
    if workflow:
        workflow.status = AgentWorkflowStatus.WAITING_FOR_INPUT
    await db.commit()
    raise InteractionPending()


async def list_interactions(db, storage, owner, run_id):
    """Return owned requests without exposing object addresses or cross-user existence."""
    run = await owned_run(db, owner, run_id)
    rows = list(
        await db.scalars(
            select(UserInteraction)
            .where(UserInteraction.run_id == run_id)
            .order_by(UserInteraction.created_at)
            .limit(100)
        )
    )
    await db.commit()
    result = []
    for row in rows:
        status = row.status
        if status == "pending" and run.status in {RunStatus.CANCELLED, RunStatus.CANCEL_REQUESTED}:
            status = "cancelled"
        result.append(
            {
                "interaction_id": row.interaction_id,
                "kind": row.kind,
                "status": status,
                "version": row.version,
                "expires_at": row.expires_at,
                **await read_body(storage, row, row.request_uri),
            }
        )
    return result


async def reply_to_interaction(db, storage, owner, run_id, identity, reply):
    """Consume a versioned reply, complete its tool and resume the saved execution."""
    run = await owned_run(db, owner, run_id)
    row = await db.scalar(
        select(UserInteraction)
        .where(
            UserInteraction.run_id == run_id,
            UserInteraction.interaction_id == identity,
            UserInteraction.owner_user_id == owner,
        )
        .with_for_update()
    )
    if row is None:
        raise ResourceNotFoundError("Interaction")
    if row.status == "resolved":
        if (
            row.reply_key != reply["key"]
            or await read_body(storage, row, row.response_uri) != reply
        ):
            raise ResourceConflictError("Interaction already has a different reply")
        await db.commit()
        return {"interaction_id": identity, "status": row.status, "version": row.version}
    if (
        run.status != RunStatus.RUNNING
        or row.status != "pending"
        or row.version != reply["expected_version"]
    ):
        raise ResourceConflictError("Interaction is no longer waiting at that version")
    if row.expires_at.replace(tzinfo=UTC) <= utc_now():
        raise ResourceConflictError("Interaction expired")
    if row.kind == "approval" and (
        not isinstance(reply.get("approved"), bool) or reply.get("text") is not None
    ):
        raise ResourceConflictError("Approval requires an explicit decision, not text")
    if row.kind == "input" and (
        not str(reply.get("text") or "").strip() or reply.get("approved") is not None
    ):
        raise ResourceConflictError("Input requires a text reply")
    row.response_uri = await put_body(storage, row, reply)
    row.reply_key, row.status = reply["key"], "resolved"
    row.version += 1
    row.resolved_at = utc_now()
    tool = await db.get(LlmModelToolCall, row.tool_call_id)
    tool.status, tool.completed_at = "completed", utc_now()
    tool.output_json = {
        "status": "completed",
        "error_code": None,
        "result": {
            "interaction_id": identity,
            "kind": row.kind,
            **(
                {"approved": reply["approved"]}
                if row.kind == "approval"
                else {"text": reply["text"]}
            ),
            "grants_additional_permissions": False,
        },
    }
    await resume_execution(db, run_id)
    await db.commit()
    return {"interaction_id": identity, "status": row.status, "version": row.version}


async def resume_execution(db, run_id):
    """Requeue the original identity and fence any consumer still unwinding the waiting call."""
    quick = await db.scalar(
        select(QuickResponseExecution).where(QuickResponseExecution.run_id == run_id)
    )
    if quick:
        quick.status, quick.lease_expires_at = "queued", None
        quick.execution_owner = None
        quick.lease_version += 1
    workflow = await db.scalar(
        select(LlmAgentWorkflowExecution).where(LlmAgentWorkflowExecution.run_id == run_id)
    )
    if workflow:
        workflow.status, workflow.execution_lease_expires_at = AgentWorkflowStatus.RUNNING, None
        workflow.execution_owner = None
        workflow.execution_lease_version += 1


async def cancel_interactions(db, run_id):
    """Cancel pending questions inside the caller's Run-cancellation transaction."""
    rows = list(
        await db.scalars(
            select(UserInteraction)
            .where(UserInteraction.run_id == run_id, UserInteraction.status == "pending")
            .with_for_update()
        )
    )
    for row in rows:
        row.status, row.resolved_at = "cancelled", utc_now()
        row.version += 1
        tool = await db.get(LlmModelToolCall, row.tool_call_id)
        tool.status, tool.completed_at = "cancelled", utc_now()
    quick = await db.scalar(
        select(QuickResponseExecution).where(QuickResponseExecution.run_id == run_id)
    )
    if quick and quick.status == "waiting_for_input":
        quick.status = "cancelled"


async def expire_interactions(factory):
    """Resume expired waits with an explicit expiry result, never with an inferred approval."""
    async with factory() as db:
        ids = list(
            await db.scalars(
                select(UserInteraction.interaction_id)
                .where(UserInteraction.status == "pending", UserInteraction.expires_at <= utc_now())
                .limit(50)
            )
        )
    for identity in ids:
        async with factory() as db:
            row = await db.get(UserInteraction, identity)
            db.info["authorized_user_id"] = row.owner_user_id
            run = await owned_run(db, row.owner_user_id, row.run_id)
            await db.refresh(row, with_for_update=True)
            if row.status != "pending":
                continue
            if run.status != RunStatus.RUNNING:
                await cancel_interactions(db, run.run_id)
            else:
                row.status, row.resolved_at = "expired", utc_now()
                row.version += 1
                tool = await db.get(LlmModelToolCall, row.tool_call_id)
                tool.status, tool.completed_at = "completed", utc_now()
                tool.output_json = {
                    "status": "completed",
                    "error_code": None,
                    "result": {
                        "interaction_id": identity,
                        "kind": row.kind,
                        "expired": True,
                        "approved": False,
                        "grants_additional_permissions": False,
                    },
                }
                await resume_execution(db, row.run_id)
            await db.commit()
