"""Read correction and business-refinement accounting from existing durable workflow facts."""

from decimal import Decimal

from sqlalchemy import or_, select

from nexuspilot_api.features.agent_runtime.schemas.agent_workflows import (
    NodeUsageRead,
    WorkflowLoopStatistics,
)
from nexuspilot_api.models import LlmAgentWorkflowEvent, LlmModelAttempt


async def read_loop_statistics(db, workflow, nodes) -> WorkflowLoopStatistics:
    """Rebuild repair calls and outcomes; overlapping usage subtotals must not be added.

    Business rounds are distinct workflow_round values on legacy plans or general
    candidates, never tool-call counts. Each node/batch repair is counted separately;
    historical events without a repair_index retain index 1. Subtotals may overlap.
    """
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
                        "agent.controller.plan_repair.requested",
                        "agent.controller.plan_repair.completed",
                        "agent.controller.plan_repair.skipped",
                    ]
                ),
            )
            .order_by(LlmAgentWorkflowEvent.event_sequence)
        )
    )
    requests = {
        (e.node_execution_id, e.public_payload_json.get("repair_index", 1))
        for e in events
        if e.event_type == "agent.node.output_repair.requested"
    }
    completed = {
        (e.node_execution_id, e.public_payload_json.get("repair_index", 1))
        for e in events
        if e.event_type == "agent.node.output_repair.completed"
    } & requests
    terminal_ids = {n.node_execution_id for n in nodes if n.status.value in {"failed", "cancelled"}}
    unknown_ids = {n.node_execution_id for n in nodes if n.status.value == "outcome_unknown"}
    terminal = {key for key in requests if key[0] in terminal_ids}
    unknown = {key for key in requests if key[0] in unknown_ids}
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
    round_key = (
        "general_agent" if workflow.workflow_name == "general_agent" else "controller_planning"
    )
    plans = [n for n in nodes if n.node_key == round_key]
    plan_rounds = {(n.input_json or {}).get("workflow_round", 1) for n in plans}
    return WorkflowLoopStatistics(
        **await read_plan_repair_statistics(db, workflow, nodes, events),
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


async def read_plan_repair_statistics(db, workflow, nodes, events) -> dict:
    """Count persisted repair requests, their validation outcomes and actual model calls.

    A skipped event without a repair node means no repair was requested. A
    skipped event with a repair node means its new plan failed validation, not
    an unexecuted call. Node completion alone never establishes plan success.
    """
    prefix = "agent.controller.plan_repair."
    requests = {e.node_execution_id for e in events if e.event_type == prefix + "requested"}
    succeeded = {
        e.public_payload_json.get("repair_node_execution_id")
        for e in events
        if e.event_type == prefix + "completed"
    } & requests
    rejected = {
        e.public_payload_json.get("repair_node_execution_id")
        for e in events
        if e.event_type == prefix + "skipped"
    } & requests
    repair_nodes = [n for n in nodes if n.node_execution_id in requests]
    request_keys = {
        f"agent:{workflow.workflow_execution_id}:{n.node_sequence}": n.node_execution_id
        for n in repair_nodes
    }
    # Match a whole node key or its round suffix, never node 1 as a prefix of 10.
    attempts = (
        list(
            await db.scalars(
                select(LlmModelAttempt)
                .where(
                    LlmModelAttempt.run_id == workflow.run_id,
                    or_(
                        *[
                            condition
                            for key in request_keys
                            for condition in (
                                LlmModelAttempt.request_key == key,
                                LlmModelAttempt.request_key.like(f"{key}:round:%"),
                            )
                        ]
                    ),
                )
                .order_by(LlmModelAttempt.started_at, LlmModelAttempt.attempt_id)
            )
        )
        if request_keys
        else []
    )
    unknown_nodes = {
        request_keys[a.request_key.split(":round:", 1)[0]]
        for a in attempts
        if a.status.value == "outcome_unknown"
    }
    outcomes = {key: 0 for key in ("failed", "cancelled", "unknown", "pending")}
    for node in repair_nodes:
        if node.node_execution_id in succeeded:
            continue
        if node.node_execution_id in rejected:
            outcome = "failed"
        elif node.node_execution_id in unknown_nodes or node.status.value == "outcome_unknown":
            outcome = "unknown"
        else:
            status = node.status.value
            if status not in {"failed", "cancelled"}:
                status = workflow.status.value
            outcome = {
                "failed": "failed",
                "cancelled": "cancelled",
                "outcome_unknown": "unknown",
            }.get(status, "pending")
        outcomes[outcome] += 1
    return {
        "plan_repair_requested_count": len(requests),
        "plan_repair_count": len(attempts),
        "plan_repair_succeeded_count": len(succeeded),
        **{f"plan_repair_{key}_count": value for key, value in outcomes.items()},
        "plan_repair_skipped_count": sum(
            e.event_type == prefix + "skipped"
            and not e.public_payload_json.get("repair_node_execution_id")
            for e in events
        ),
        "plan_repair_attempt_ids": [a.attempt_id for a in attempts],
        "plan_repair_usage": NodeUsageRead(
            model_call_count=len(attempts),
            input_tokens=sum(a.input_tokens or 0 for a in attempts),
            output_tokens=sum(a.output_tokens or 0 for a in attempts),
            cached_tokens=sum(a.cached_tokens or 0 for a in attempts),
            estimated_cost=sum((a.estimated_cost or Decimal("0") for a in attempts), Decimal("0")),
        ),
    }
