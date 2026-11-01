"""Bounded App diagnostics using structural SQL columns only; never hydrate stored bodies."""

from sqlalchemy import select, tuple_

from nexuspilot_api.core.errors import ResourceNotFoundError
from nexuspilot_api.features.identity.models.applications import RunApplicationContext
from nexuspilot_api.features.memory.models.product_memory import (
    UserMemoryEntry,
    UserMemoryOperation,
    UserMemoryScope,
)
from nexuspilot_api.models import (
    LlmAgentWorkflowExecution,
    LlmModelAttempt,
    LlmModelToolCall,
    LlmRun,
)


async def diagnostic_page(db, statement, *, model, id_column, cursor, limit):
    """Page a Run-constrained query by saved start time and ID; foreign cursors are not accepted."""
    if cursor:
        position = (await db.execute(statement.where(id_column == cursor))).mappings().one_or_none()
        if position is None:
            raise ResourceNotFoundError("Diagnostic cursor")
        statement = statement.where(
            tuple_(model.started_at, id_column) > (position["started_at"], cursor)
        )
    rows = (
        (await db.execute(statement.order_by(model.started_at, id_column).limit(limit + 1)))
        .mappings()
        .all()
    )
    return {
        "items": [dict(row) for row in rows[:limit]],
        "next_cursor": rows[limit - 1][id_column.key] if len(rows) > limit else None,
    }


async def attach_memory_write_receipts(db, identity, tool_page):
    """Project committed writes for this authorized tool page, never model output or memory text.

    The immutable operation version identifies what this call saved. A later edit disables
    the undo hint; the mutation endpoint still performs its own transactional version check.
    """
    calls = {item["tool_call_id"]: item for item in tool_page["items"]
             if item["tool_name"] in {"save_memory", "propose_memory", "forget_memory"}}
    if not calls:
        return
    rows = (await db.execute(select(UserMemoryOperation.request_key,
                                     UserMemoryOperation.result_json,
                                     UserMemoryOperation.scope_id)
        .join(UserMemoryScope, UserMemoryScope.scope_id == UserMemoryOperation.scope_id)
        .where(UserMemoryOperation.request_key.in_(calls),
               UserMemoryScope.owner_user_id == identity.pilot_principal_id,
               UserMemoryScope.entry == identity.application_id,
               UserMemoryScope.project_id.is_(None)))).mappings().all()
    for row in rows:
        result = row["result_json"]
        entry_id = result.get("entry_id")
        applied = result.get("applied_version", result.get("version"))
        if not entry_id or type(applied) is not int:
            continue
        entry = (await db.execute(select(UserMemoryEntry.version)
            .where(UserMemoryEntry.entry_id == entry_id,
                   UserMemoryEntry.scope_id == row["scope_id"]))).scalar_one_or_none()
        if entry is None:
            continue
        calls[row["request_key"]]["memory_write"] = {
            "entry_id": entry_id,
            "applied_version": applied,
            "current_version": entry,
            "status": result.get("status"),
            "can_undo": applied == entry,
        }


async def read_application_diagnostics(
    db, identity, run_id, *, limit, after_attempt_id, after_tool_call_id
):
    """Correlate one App Run without expanding account links into cross-App read authority."""
    context = await db.get(RunApplicationContext, run_id)
    if context is None or context.mapping_id != identity.mapping_id:
        raise ResourceNotFoundError("Application Run")
    run = (
        (
            await db.execute(
                select(LlmRun.run_id, LlmRun.status, LlmRun.started_at, LlmRun.completed_at).where(
                    LlmRun.run_id == run_id, LlmRun.user_id == identity.pilot_principal_id
                )
            )
        )
        .mappings()
        .one_or_none()
    )
    if run is None:
        raise ResourceNotFoundError("Application Run")
    workflow = (
        (
            await db.execute(
                select(
                    LlmAgentWorkflowExecution.workflow_execution_id,
                    LlmAgentWorkflowExecution.status,
                    LlmAgentWorkflowExecution.started_at,
                    LlmAgentWorkflowExecution.completed_at,
                    LlmAgentWorkflowExecution.error_code,
                ).where(LlmAgentWorkflowExecution.run_id == run_id)
            )
        )
        .mappings()
        .one_or_none()
    )
    attempts = select(
        LlmModelAttempt.attempt_id,
        LlmModelAttempt.task_id,
        LlmModelAttempt.context_build_id,
        LlmModelAttempt.provider,
        LlmModelAttempt.model,
        LlmModelAttempt.agent_role,
        LlmModelAttempt.status,
        LlmModelAttempt.started_at,
        LlmModelAttempt.completed_at,
        LlmModelAttempt.latency_ms,
        LlmModelAttempt.input_tokens,
        LlmModelAttempt.output_tokens,
        LlmModelAttempt.cached_tokens,
        LlmModelAttempt.error_code,
    ).where(LlmModelAttempt.run_id == run_id)
    tools = (
        select(
            LlmModelToolCall.tool_call_id,
            LlmModelToolCall.attempt_id,
            LlmModelToolCall.tool_name,
            LlmModelToolCall.tool_version,
            LlmModelToolCall.status,
            LlmModelToolCall.started_at,
            LlmModelToolCall.completed_at,
            LlmModelToolCall.error_code,
        )
        .join(LlmModelAttempt, LlmModelAttempt.attempt_id == LlmModelToolCall.attempt_id)
        .where(LlmModelAttempt.run_id == run_id)
    )
    tool_page = await diagnostic_page(
        db, tools, model=LlmModelToolCall, id_column=LlmModelToolCall.tool_call_id,
        cursor=after_tool_call_id, limit=limit,
    )
    await attach_memory_write_receipts(db, identity, tool_page)
    return {
        "application_id": identity.application_id,
        "run": dict(run),
        "workflow": dict(workflow) if workflow else None,
        "attempts": await diagnostic_page(
            db,
            attempts,
            model=LlmModelAttempt,
            id_column=LlmModelAttempt.attempt_id,
            cursor=after_attempt_id,
            limit=limit,
        ),
        "tools": tool_page,
    }
