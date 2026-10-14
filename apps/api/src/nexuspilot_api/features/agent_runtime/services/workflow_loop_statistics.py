"""Read correction and business-refinement accounting from existing durable workflow facts."""

from decimal import Decimal

from sqlalchemy import select

from nexuspilot_api.features.agent_runtime.schemas.agent_workflows import (
    NodeUsageRead,
    WorkflowLoopStatistics,
)
from nexuspilot_api.models import LlmAgentWorkflowEvent, LlmModelAttempt


async def read_loop_statistics(db, workflow, nodes) -> WorkflowLoopStatistics:
    """Count attempts and distinct business rounds; same-round plan repairs are not refinement."""
    events = list(
        await db.scalars(
            select(LlmAgentWorkflowEvent)
            .where(
                LlmAgentWorkflowEvent.workflow_execution_id == workflow.workflow_execution_id,
                LlmAgentWorkflowEvent.event_type.in_(
                    [
                        "agent.node.output_repair.requested",
                        "agent.node.output_repair.completed",
                        "agent.workflow.refinement.requested",
                        "agent.workflow.refinement.skipped",
                    ]
                ),
            )
            .order_by(LlmAgentWorkflowEvent.event_sequence)
        )
    )
    requests = {
        e.node_execution_id for e in events if e.event_type == "agent.node.output_repair.requested"
    }
    completed = {
        e.node_execution_id for e in events if e.event_type == "agent.node.output_repair.completed"
    }
    terminal = {n.node_execution_id for n in nodes if n.status.value in {"failed", "cancelled"}}
    unknown = {n.node_execution_id for n in nodes if n.status.value == "outcome_unknown"}
    attempts = list(
        await db.scalars(
            select(LlmModelAttempt)
            .where(
                LlmModelAttempt.run_id == workflow.run_id,
                LlmModelAttempt.request_key.like(
                    f"agent:{workflow.workflow_execution_id}:%:round:repair:1"
                ),
            )
            .order_by(LlmModelAttempt.started_at, LlmModelAttempt.attempt_id)
        )
    )
    refined = [
        n
        for n in nodes
        if (n.input_json or {}).get("workflow_round", 1) > 1
        and n.output_type not in {"final_synthesis", "workflow_completion"}
    ]
    refined_usages = [NodeUsageRead.model_validate(n.usage_json or {}) for n in refined]
    plans = [n for n in nodes if n.node_key == "controller_planning"]
    plan_rounds = {(n.input_json or {}).get("workflow_round", 1) for n in plans}
    return WorkflowLoopStatistics(
        workflow_round_count=len(plan_rounds),
        refinement_count=max(0, len(plan_rounds) - 1),
        refinement_skipped_count=sum(
            e.event_type == "agent.workflow.refinement.skipped" for e in events
        ),
        output_repair_requested_count=len(requests),
        output_repair_count=len(attempts),
        output_repair_succeeded_count=len(completed),
        output_repair_failed_count=len((requests - completed) & terminal),
        output_repair_pending_count=len(requests - completed - terminal - unknown),
        output_repair_unknown_count=len((requests - completed) & unknown),
        repair_attempt_ids=[a.attempt_id for a in attempts],
        repair_usage=NodeUsageRead(
            model_call_count=len(attempts),
            input_tokens=sum(a.input_tokens or 0 for a in attempts),
            output_tokens=sum(a.output_tokens or 0 for a in attempts),
            cached_tokens=sum(a.cached_tokens or 0 for a in attempts),
            estimated_cost=sum((a.estimated_cost or Decimal("0") for a in attempts), Decimal("0")),
        ),
        refinement_usage=NodeUsageRead(
            model_call_count=sum(u.model_call_count for u in refined_usages),
            tool_call_count=sum(u.tool_call_count for u in refined_usages),
            input_tokens=sum(u.input_tokens for u in refined_usages),
            output_tokens=sum(u.output_tokens for u in refined_usages),
            cached_tokens=sum(u.cached_tokens for u in refined_usages),
            estimated_cost=sum((u.estimated_cost for u in refined_usages), Decimal("0")),
        ),
        events=[
            {
                "event_id": e.event_id,
                "event_sequence": e.event_sequence,
                "event_type": e.event_type,
                "node_execution_id": e.node_execution_id,
                "occurred_at": e.occurred_at.isoformat(),
                **e.public_payload_json,
            }
            for e in events
        ],
    )
