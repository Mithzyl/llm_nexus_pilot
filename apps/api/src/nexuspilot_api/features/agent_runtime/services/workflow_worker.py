"""Execute durable Workflow acceptance outside HTTP; never restart ambiguous external calls.

This consumer polls the transactional Outbox. General Agent/reviewer tool loops
and saved final nodes have explicit recovery paths; other boundaries stop without
replaying unknown effects. RabbitMQ delivery remains a separate rollout requirement.
"""

import asyncio

from sqlalchemy import or_, select

from nexuspilot_api.features.agent_runtime.schemas.agent_workflows import (
    AgentWorkflowCreate,
    WorkflowErrorRead,
)
from nexuspilot_api.features.agent_runtime.services.general_agent_orchestrator import (
    GeneralAgentOrchestrator,
)
from nexuspilot_api.features.agent_runtime.services.tool_loop_checkpoint import load_loop_checkpoint
from nexuspilot_api.features.agent_runtime.services.workflow_execution_service import (
    AgentWorkflowExecutionService,
)
from nexuspilot_api.features.agent_runtime.services.workflow_ownership import renew_claim
from nexuspilot_api.models import (
    LlmAgentWorkflowExecution,
    LlmAgentWorkflowNodeExecution,
    LlmModelAttempt,
    LlmModelToolCall,
    LlmRun,
)


async def execute_claim(factory, storage, registry, prices, claim):
    """Execute a fresh job or a registered loop/final checkpoint under one ownership claim.

    Other interrupted nodes need complete checkpoint reconstruction. Stop explicitly
    rather than rerun model calls or tools; observations alone do not authorize replay.
    """

    async def execute():
        """Bind persisted ownership and credentials to a separate execution session."""
        async with factory() as db:
            db.info["workflow_execution_claim"] = claim
            db.info["body_storage"] = storage
            workflow = await db.get(LlmAgentWorkflowExecution, claim.workflow_id)
            run = await db.get(LlmRun, workflow.run_id)
            db.info["authorized_user_id"] = run.user_id
            service = AgentWorkflowExecutionService(
                registry=registry,
                prices=prices,
                db_session=db,
                db_session_factory=factory,
                storage=storage,
            )
            existing = await db.scalar(
                select(
                    LlmAgentWorkflowNodeExecution.node_execution_id,
                )
                .where(
                    LlmAgentWorkflowNodeExecution.workflow_execution_id == claim.workflow_id,
                )
                .limit(1)
            )
            if existing:
                unknown = await db.scalar(
                    select(LlmModelAttempt.attempt_id)
                    .where(
                        LlmModelAttempt.run_id == workflow.run_id,
                        LlmModelAttempt.status.in_(["STARTED", "OUTCOME_UNKNOWN"]),
                    )
                    .limit(1)
                )
                unknown_tool = await db.scalar(
                    select(LlmModelToolCall.tool_call_id)
                    .join(
                        LlmModelAttempt,
                        LlmModelAttempt.attempt_id == LlmModelToolCall.attempt_id,
                    )
                    .where(
                        LlmModelAttempt.run_id == workflow.run_id,
                        LlmModelToolCall.status.in_(["requested", "running", "outcome_unknown"]),
                        or_(
                            LlmModelToolCall.tool_name != "delegate_task",
                            LlmModelToolCall.status == "outcome_unknown",
                        ),
                    )
                    .limit(1)
                )
                outcome_known = unknown is None and unknown_tool is None
                if outcome_known and workflow.workflow_name == "general_agent":
                    orchestrator = GeneralAgentOrchestrator(
                        registry=registry,
                        prices=prices,
                        db_session=db,
                        db_session_factory=factory,
                        storage=storage,
                    )
                    if await orchestrator.resume_saved_finalization(workflow):
                        return
                    checkpoint = await load_loop_checkpoint(db, storage, workflow)
                    if checkpoint is not None:
                        await orchestrator.execute(
                            workflow,
                            AgentWorkflowCreate.model_validate(workflow.request_json),
                            resume=checkpoint,
                        )
                        return
                    # The first node may be committed before its first checkpoint.
                    # Only an untouched, task-bound main node can be bootstrapped.
                    nodes = list(
                        await db.scalars(
                            select(LlmAgentWorkflowNodeExecution).where(
                                LlmAgentWorkflowNodeExecution.workflow_execution_id
                                == claim.workflow_id,
                            )
                        )
                    )
                    any_attempt = await db.scalar(
                        select(LlmModelAttempt.attempt_id)
                        .where(
                            LlmModelAttempt.run_id == workflow.run_id,
                        )
                        .limit(1)
                    )
                    if (
                        len(nodes) == 1
                        and nodes[0].node_key == "general_agent"
                        and nodes[0].task_id
                        and nodes[0].status.value == "running"
                        and any_attempt is None
                    ):
                        await orchestrator.execute(
                            workflow, AgentWorkflowCreate.model_validate(workflow.request_json)
                        )
                        return
                await service.workflow_state.fail_workflow(
                    workflow,
                    error=WorkflowErrorRead(
                        error_code="workflow_checkpoint_recovery_unavailable",
                        error_type="execution_recovery",
                        is_retryable=False,
                        outcome_is_known=outcome_known,
                        public_message=(
                            "Checkpoint recovery is unavailable; no calls were replayed."
                        ),
                    ),
                )
                return
            await service.execute_prepared_workflow(
                workflow, AgentWorkflowCreate.model_validate(workflow.request_json)
            )

    running = asyncio.create_task(execute())

    async def renew():
        """Cancel local execution when renewal fails; SQL fencing rejects late writes."""
        try:
            while not running.done():
                await asyncio.sleep(1)
                if not await renew_claim(factory, claim):
                    running.cancel()
                    return
        except Exception:
            running.cancel()
            raise

    heartbeat = asyncio.create_task(renew())
    try:
        await running
    finally:
        heartbeat.cancel()
        await asyncio.gather(heartbeat, return_exceptions=True)
