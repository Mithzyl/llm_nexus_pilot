"""Read Agent workflow summaries, complete node results, and replayable events."""

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nexuspilot_api.core.errors import InvalidCursorError, ResourceNotFoundError
from nexuspilot_api.core.pagination import CursorCodec, DatabaseSequencePaginationKey
from nexuspilot_api.features.agent_runtime.schemas.agent_workflows import (
    AgentWorkflowEventRead,
    AgentWorkflowNodeResultRead,
    AgentWorkflowResultRead,
    AgentWorkflowSummaryRead,
    FinalSynthesisOutput,
    NodeBudgetRead,
    NodeEvidenceRead,
    NodeInputRead,
    NodeObservabilityRead,
    NodePublicViewRead,
    NodeTimingRead,
    NodeTransitionRead,
    NodeUsageRead,
    WorkflowCompletionOutput,
    WorkflowErrorRead,
)
from nexuspilot_api.models import (
    LlmAgentWorkflowEvent,
    LlmAgentWorkflowExecution,
    LlmAgentWorkflowNodeExecution,
)
from nexuspilot_api.schemas.pagination import CursorPage


async def require_workflow(
    db_session: AsyncSession,
    workflow_execution_id: str,
) -> LlmAgentWorkflowExecution:
    """Return one durable workflow or raise the stable resource-not-found error."""

    workflow = await db_session.get(LlmAgentWorkflowExecution, workflow_execution_id)
    if workflow is None:
        raise ResourceNotFoundError("Agent Workflow")
    # SQLAlchemy expires server-on-update columns after commit even when the session
    # factory uses expire_on_commit=False; refresh before synchronous schema mapping.
    await db_session.refresh(workflow)
    return workflow


async def get_workflow_summary(
    db_session: AsyncSession,
    workflow_execution_id: str,
) -> AgentWorkflowSummaryRead:
    """Return current workflow position without loading complete node payloads."""

    workflow = await require_workflow(db_session, workflow_execution_id)
    node_count = await _workflow_node_count(db_session, workflow_execution_id)
    return _workflow_summary(workflow, node_count=node_count)


async def get_run_workflow_summary(
    db_session: AsyncSession,
    run_id: str,
) -> AgentWorkflowSummaryRead:
    """Discover the single Agent workflow owned by a Run and return its summary."""

    workflow = await db_session.scalar(
        select(LlmAgentWorkflowExecution).where(LlmAgentWorkflowExecution.run_id == run_id)
    )
    if workflow is None:
        raise ResourceNotFoundError("Agent Workflow")
    await db_session.refresh(workflow)
    node_count = await _workflow_node_count(db_session, workflow.workflow_execution_id)
    return _workflow_summary(workflow, node_count=node_count)


async def get_workflow_result(
    db_session: AsyncSession,
    workflow_execution_id: str,
) -> AgentWorkflowResultRead:
    """Return one bounded snapshot containing every committed node in sequence order."""

    workflow = await require_workflow(db_session, workflow_execution_id)
    nodes = list(
        (
            await db_session.scalars(
                select(LlmAgentWorkflowNodeExecution)
                .where(LlmAgentWorkflowNodeExecution.workflow_execution_id == workflow_execution_id)
                .order_by(LlmAgentWorkflowNodeExecution.node_sequence)
                .limit(workflow.max_nodes + 1)
            )
        ).all()
    )
    if len(nodes) > workflow.max_nodes:
        raise RuntimeError("Persisted workflow exceeds its declared node limit")
    node_results = [_node_result(workflow, node) for node in nodes]
    final_output = next(
        (
            FinalSynthesisOutput.model_validate(node.output_json)
            for node in reversed(nodes)
            if node.output_type == "final_synthesis" and node.output_json is not None
        ),
        None,
    )
    completion = next(
        (
            WorkflowCompletionOutput.model_validate(node.output_json)
            for node in reversed(nodes)
            if node.output_type == "workflow_completion" and node.output_json is not None
        ),
        None,
    )
    aggregate_usage = NodeUsageRead(
        input_tokens=sum(node.usage.input_tokens for node in node_results),
        output_tokens=sum(node.usage.output_tokens for node in node_results),
        cached_tokens=sum(node.usage.cached_tokens for node in node_results),
        estimated_cost=sum(
            (node.usage.estimated_cost for node in node_results),
            Decimal("0"),
        ),
        model_call_count=sum(node.usage.model_call_count for node in node_results),
        tool_call_count=sum(node.usage.tool_call_count for node in node_results),
    )
    return AgentWorkflowResultRead(
        **_workflow_summary(workflow, node_count=len(nodes)).model_dump(),
        nodes=node_results,
        final_output=final_output,
        completion=completion,
        aggregate_usage=aggregate_usage,
        warnings=[warning for node in node_results for warning in node.warnings],
        trace_id=workflow.trace_id,
    )


async def list_workflow_nodes(
    db_session: AsyncSession,
    workflow_execution_id: str,
    *,
    codec: CursorCodec,
    cursor: str | None,
    limit: int,
) -> CursorPage[AgentWorkflowNodeResultRead]:
    """Return an ordered cursor page of complete node result envelopes."""

    workflow = await require_workflow(db_session, workflow_execution_id)
    after_key = codec.decode_sequence(cursor) if cursor else None
    if after_key and after_key.scope_id != workflow_execution_id:
        raise InvalidCursorError
    statement = select(LlmAgentWorkflowNodeExecution).where(
        LlmAgentWorkflowNodeExecution.workflow_execution_id == workflow_execution_id
    )
    if after_key is not None:
        statement = statement.where(
            LlmAgentWorkflowNodeExecution.node_sequence > after_key.sequence
        )
    nodes = list(
        (
            await db_session.scalars(
                statement.order_by(LlmAgentWorkflowNodeExecution.node_sequence).limit(limit + 1)
            )
        ).all()
    )
    has_more = len(nodes) > limit
    items = nodes[:limit]
    next_cursor = None
    if has_more and items:
        next_cursor = codec.encode_sequence(
            DatabaseSequencePaginationKey(
                scope_id=workflow_execution_id,
                sequence=items[-1].node_sequence,
            )
        )
    return CursorPage[AgentWorkflowNodeResultRead](
        items=[_node_result(workflow, node) for node in items],
        next_cursor=next_cursor,
        has_more=next_cursor is not None,
        limit=limit,
    )


async def get_workflow_node(
    db_session: AsyncSession,
    workflow_execution_id: str,
    node_execution_id: str,
) -> AgentWorkflowNodeResultRead:
    """Return one node only when it belongs to the requested workflow."""

    workflow = await require_workflow(db_session, workflow_execution_id)
    node = await db_session.scalar(
        select(LlmAgentWorkflowNodeExecution).where(
            LlmAgentWorkflowNodeExecution.node_execution_id == node_execution_id,
            LlmAgentWorkflowNodeExecution.workflow_execution_id == workflow_execution_id,
        )
    )
    if node is None:
        raise ResourceNotFoundError("Agent Workflow Node")
    return _node_result(workflow, node)


async def list_workflow_events(
    db_session: AsyncSession,
    workflow_execution_id: str,
    *,
    after_sequence: int,
    limit: int = 1_000,
) -> list[AgentWorkflowEventRead]:
    """Return persisted events strictly after one acknowledged sequence."""

    await require_workflow(db_session, workflow_execution_id)
    events = list(
        (
            await db_session.scalars(
                select(LlmAgentWorkflowEvent)
                .where(
                    LlmAgentWorkflowEvent.workflow_execution_id == workflow_execution_id,
                    LlmAgentWorkflowEvent.event_sequence > after_sequence,
                )
                .order_by(LlmAgentWorkflowEvent.event_sequence)
                .limit(limit)
            )
        ).all()
    )
    return [_event_read(event) for event in events]


def _workflow_summary(
    workflow: LlmAgentWorkflowExecution,
    *,
    node_count: int,
) -> AgentWorkflowSummaryRead:
    """Map one mutable workflow row to its stable public summary contract."""

    return AgentWorkflowSummaryRead(
        workflow_execution_id=workflow.workflow_execution_id,
        run_id=workflow.run_id,
        workflow_name=workflow.workflow_name,
        workflow_version=workflow.workflow_version,
        execution_profile=workflow.execution_profile,
        status=workflow.status,
        version=workflow.version,
        snapshot_version=workflow.snapshot_version,
        current_stage=workflow.current_stage,
        primary_node_execution_id=workflow.primary_node_execution_id,
        active_node_execution_ids=list(workflow.active_node_execution_ids_json or []),
        model_call_count=workflow.model_call_count,
        max_model_calls=workflow.max_model_calls,
        node_count=node_count,
        max_nodes=workflow.max_nodes,
        started_at=workflow.started_at,
        completed_at=workflow.completed_at,
        created_at=workflow.created_at,
        updated_at=workflow.updated_at,
        error=(
            WorkflowErrorRead.model_validate(workflow.error_json) if workflow.error_json else None
        ),
    )


def _node_result(
    workflow: LlmAgentWorkflowExecution,
    node: LlmAgentWorkflowNodeExecution,
) -> AgentWorkflowNodeResultRead:
    """Map persisted node indexes and JSON values into the complete result envelope."""

    completed_at = node.completed_at
    elapsed_end = completed_at or datetime.now(UTC)
    started_at = _as_utc(node.started_at)
    elapsed_end = _as_utc(elapsed_end)
    duration_ms = node.duration_ms
    if duration_ms is None:
        duration_ms = max(0, int((elapsed_end - started_at).total_seconds() * 1_000))
    input_data = {**(node.input_json or {}), "input_hash": node.input_hash}
    return AgentWorkflowNodeResultRead(
        workflow_execution_id=workflow.workflow_execution_id,
        workflow_name=workflow.workflow_name,
        workflow_version=workflow.workflow_version,
        node_execution_id=node.node_execution_id,
        node_key=node.node_key,
        node_type=node.node_type,
        node_version=node.node_version,
        node_sequence=node.node_sequence,
        node_attempt=node.node_attempt,
        parent_node_execution_id=node.parent_node_execution_id,
        run_id=node.run_id,
        task_id=node.task_id,
        agent_run_id=node.agent_run_id,
        agent_turn_id=node.agent_turn_id,
        agent_role=node.agent_role,
        status=node.status,
        input=NodeInputRead.model_validate(input_data),
        output_type=node.output_type,
        output_schema_version=node.output_schema_version,
        output=node.output_json,
        transition=NodeTransitionRead.model_validate(node.transition_json or {}),
        evidence=NodeEvidenceRead.model_validate(node.evidence_json or {}),
        usage=NodeUsageRead.model_validate(node.usage_json or {}),
        budget=NodeBudgetRead.model_validate(node.budget_json),
        timing=NodeTimingRead(
            started_at=node.started_at,
            completed_at=node.completed_at,
            duration_ms=duration_ms,
        ),
        public_view=NodePublicViewRead.model_validate(node.public_view_json),
        observability=NodeObservabilityRead(trace_id=node.trace_id, span_id=node.span_id),
        warnings=list(node.warnings_json or []),
        error=(WorkflowErrorRead.model_validate(node.error_json) if node.error_json else None),
        created_at=node.created_at,
    )


def _event_read(event: LlmAgentWorkflowEvent) -> AgentWorkflowEventRead:
    """Map one immutable event row to the public replay contract."""

    return AgentWorkflowEventRead(
        event_id=event.event_id,
        event_sequence=event.event_sequence,
        workflow_execution_id=event.workflow_execution_id,
        run_id=event.run_id,
        node_execution_id=event.node_execution_id,
        event_type=event.event_type,
        workflow_status=event.workflow_status,
        node_status=event.node_status,
        occurred_at=event.occurred_at,
        public_summary=event.public_summary,
        public_payload=event.public_payload_json,
        trace_id=event.trace_id,
    )


async def _workflow_node_count(
    db_session: AsyncSession,
    workflow_execution_id: str,
) -> int:
    """Count bounded node rows without loading their JSON output bodies."""

    from sqlalchemy import func

    return int(
        await db_session.scalar(
            select(func.count(LlmAgentWorkflowNodeExecution.node_execution_id)).where(
                LlmAgentWorkflowNodeExecution.workflow_execution_id == workflow_execution_id
            )
        )
        or 0
    )


def _as_utc(value: datetime) -> datetime:
    """Treat SQLite-naive persisted UTC values as UTC for elapsed calculations."""

    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
