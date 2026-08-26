"""Persist shared Agent workflow nodes, budgets, and committed public events."""

import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from decimal import Decimal

from pydantic import BaseModel
from sqlalchemy import case, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from nexuspilot_api.core.errors import (
    InvalidRequestError,
    ResourceConflictError,
    ResourceNotFoundError,
)
from nexuspilot_api.features.agent_runtime.schemas.agent_workflows import (
    AgentWorkflowCreate,
    AgentWorkflowEventRead,
    FinalSynthesisModelOutput,
    NodeBudgetRead,
    NodeEvidenceRead,
    NodePublicViewRead,
    NodeTransitionRead,
    NodeUsageRead,
    WorkflowErrorRead,
    node_output_schema_version,
    validate_node_output,
)
from nexuspilot_api.features.agent_runtime.services.workflow_definition_registry import (
    AgentWorkflowDefinition,
    WorkflowDefinitionRegistry,
)
from nexuspilot_api.features.agent_runtime.services.workflow_policy import (
    hash_node_input,
    hash_workflow_request,
)
from nexuspilot_api.models import (
    AgentRunStatus,
    AgentTurnStatus,
    AgentTurnType,
    AgentWorkflowNodeStatus,
    AgentWorkflowStatus,
    AttemptStatus,
    LlmAgentRun,
    LlmAgentTurn,
    LlmAgentWorkflowEvent,
    LlmAgentWorkflowExecution,
    LlmAgentWorkflowNodeExecution,
    LlmModelAttempt,
    LlmRun,
    LlmTask,
    LlmTaskDependency,
    MessageRole,
    RunStatus,
    TaskStatus,
    new_id,
)
from nexuspilot_api.models.base import utc_now
from nexuspilot_api.schemas.sessions import MessageCreate
from nexuspilot_api.services.session_service import create_message

EventSink = Callable[[AgentWorkflowEventRead], Awaitable[None]]
MAX_NODE_RESULT_BYTES = 65_536
_TERMINAL_WORKFLOW_STATUSES = frozenset(
    {
        AgentWorkflowStatus.COMPLETED,
        AgentWorkflowStatus.FAILED,
        AgentWorkflowStatus.CANCELLED,
        AgentWorkflowStatus.OUTCOME_UNKNOWN,
    }
)
_TERMINAL_AGENT_RUN_STATUSES = frozenset(
    {
        AgentRunStatus.COMPLETED,
        AgentRunStatus.FAILED,
        AgentRunStatus.CANCELLED,
    }
)
_TERMINAL_TASK_STATUSES = frozenset(
    {
        TaskStatus.COMPLETED,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
    }
)


class WorkflowExecutionStopped(Exception):
    """Stop expected workflow processing after a durable terminal transition."""


class WorkflowExecutionStateService:
    """Own workflow guards, budget projections, versions, and committed event delivery."""

    def __init__(
        self,
        *,
        db_session: AsyncSession,
        workflow_definitions: WorkflowDefinitionRegistry,
        event_sink: EventSink | None = None,
    ) -> None:
        """Bind one execution-scoped database session and optional live event observer."""

        self.db_session = db_session
        self.workflow_definitions = workflow_definitions
        self.event_sink = event_sink

    def set_event_sink(self, event_sink: EventSink | None) -> None:
        """Bind an observer to this execution without sharing it across requests."""

        self.event_sink = event_sink

    async def create_workflow(
        self,
        run_id: str,
        payload: AgentWorkflowCreate,
    ) -> tuple[LlmAgentWorkflowExecution, bool]:
        """Validate Run state and atomically create or replay one workflow fact."""

        self.workflow_definitions.require_definition(
            payload.workflow_name,
            payload.workflow_version,
            payload.execution_profile,
        )
        run = await self.db_session.scalar(
            select(LlmRun).where(LlmRun.run_id == run_id).with_for_update()
        )
        if run is None:
            raise ResourceNotFoundError("Run")
        request_hash = hash_workflow_request(payload)
        existing = await self.db_session.scalar(
            select(LlmAgentWorkflowExecution).where(
                LlmAgentWorkflowExecution.run_id == run_id,
                LlmAgentWorkflowExecution.idempotency_key == payload.idempotency_key,
            )
        )
        if existing is not None:
            if existing.request_hash != request_hash:
                raise ResourceConflictError(
                    "Agent workflow idempotency key was reused with another request"
                )
            # Release the parent Run lock while keeping the identity usable with
            # request sessions configured with expire_on_commit=False.
            await self.db_session.commit()
            return existing, True

        existing_run_workflow_id = await self.db_session.scalar(
            select(LlmAgentWorkflowExecution.workflow_execution_id).where(
                LlmAgentWorkflowExecution.run_id == run_id
            )
        )
        if existing_run_workflow_id is not None:
            raise ResourceConflictError("Run already belongs to another Agent workflow execution")
        if run.status not in {RunStatus.PENDING, RunStatus.RUNNING}:
            raise ResourceConflictError(
                f"Cannot start an Agent workflow for run in {run.status.value} status"
            )

        now = utc_now()
        workflow = LlmAgentWorkflowExecution(
            workflow_execution_id=new_id(),
            run_id=run_id,
            workflow_name=payload.workflow_name,
            workflow_version=payload.workflow_version,
            execution_profile=payload.execution_profile,
            status=AgentWorkflowStatus.RUNNING,
            version=1,
            snapshot_version=1,
            current_stage="workflow_start",
            active_node_execution_ids_json=[],
            role_bindings_json={
                role: binding.model_dump(mode="json")
                for role, binding in payload.role_bindings.items()
            },
            request_json=payload.model_dump(mode="json", exclude={"stream"}),
            review_policy=payload.review_policy,
            idempotency_key=payload.idempotency_key,
            request_hash=request_hash,
            max_nodes=payload.max_nodes,
            max_model_calls=payload.max_model_calls,
            max_parallel_agents=payload.max_parallel_agents,
            wall_time_limit_ms=payload.wall_time_limit_ms,
            started_at=now,
        )
        self.db_session.add(workflow)
        run.status = RunStatus.RUNNING
        run.started_at = run.started_at or now
        try:
            await self.db_session.flush()
            await self.append_event(
                workflow,
                event_type="agent.workflow.started",
                public_summary="Agent workflow started",
                public_payload={"execution_profile": payload.execution_profile},
            )
        except IntegrityError as exc:
            await self.db_session.rollback()
            existing = await self.db_session.scalar(
                select(LlmAgentWorkflowExecution).where(
                    LlmAgentWorkflowExecution.run_id == run_id,
                    LlmAgentWorkflowExecution.idempotency_key == payload.idempotency_key,
                )
            )
            if existing is not None and existing.request_hash == request_hash:
                return existing, True
            raise ResourceConflictError("Agent workflow creation conflicted") from exc
        return workflow, False

    async def start_node(
        self,
        workflow: LlmAgentWorkflowExecution,
        *,
        node_key: str,
        node_type: str,
        output_type: str,
        input_values: dict,
        status_label: str,
        summary: str,
        source_node_ids: list[str] | None = None,
        handoff_ids: list[str] | None = None,
        task_id: str | None = None,
        agent_run_id: str | None = None,
        agent_turn_id: str | None = None,
        agent_role: str | None = None,
    ) -> LlmAgentWorkflowNodeExecution:
        """Persist a running node and start event before any model side effect."""

        await self.ensure_workflow_running(workflow)
        definition = self._require_workflow_definition(workflow)
        definition.validate_node(
            node_key=node_key,
            node_type=node_type,
            output_type=output_type,
        )
        node_count = int(
            await self.db_session.scalar(
                select(func.count(LlmAgentWorkflowNodeExecution.node_execution_id)).where(
                    LlmAgentWorkflowNodeExecution.workflow_execution_id
                    == workflow.workflow_execution_id
                )
            )
            or 0
        )
        if node_count >= workflow.max_nodes:
            raise InvalidRequestError("Agent workflow reached its node limit")
        node_sequence = node_count + 1
        source_ids = source_node_ids or []
        input_payload = {
            "schema_version": f"{output_type}_input.v1",
            "source_node_execution_ids": source_ids,
            "message_ids": [],
            "artifact_ids": [],
            "handoff_ids": handoff_ids or [],
            "context_build_id": None,
            "prompt_release_id": None,
            "values": input_values,
        }
        node = LlmAgentWorkflowNodeExecution(
            node_execution_id=new_id(),
            workflow_execution_id=workflow.workflow_execution_id,
            run_id=workflow.run_id,
            node_key=node_key,
            node_type=node_type,
            node_version="1.0.0",
            node_sequence=node_sequence,
            node_attempt=1,
            parent_node_execution_id=source_ids[-1] if source_ids else None,
            task_id=task_id,
            agent_run_id=agent_run_id,
            agent_turn_id=agent_turn_id,
            agent_role=agent_role,
            status=AgentWorkflowNodeStatus.RUNNING,
            input_schema_version=input_payload["schema_version"],
            input_hash=hash_node_input(input_payload),
            input_json={key: value for key, value in input_payload.items() if key != "values"},
            output_type=output_type,
            output_schema_version=node_output_schema_version(output_type),
            output_json=None,
            transition_json={},
            evidence_json=NodeEvidenceRead().model_dump(mode="json"),
            usage_json=NodeUsageRead().model_dump(mode="json"),
            budget_json=(
                await self.build_node_budget(workflow, node_count=node_count + 1)
            ).model_dump(mode="json"),
            public_view_json=NodePublicViewRead(
                status_label=status_label,
                summary=summary,
                progress_current=node_sequence,
                progress_total=_progress_total(workflow, node_sequence),
            ).model_dump(mode="json"),
            warnings_json=[],
            started_at=utc_now(),
        )
        self.db_session.add(node)
        # Event has a database foreign key but no ORM relationship, so the node
        # must flush before its start event is inserted.
        await self.db_session.flush()
        active_ids = list(workflow.active_node_execution_ids_json or [])
        active_ids.append(node.node_execution_id)
        workflow.active_node_execution_ids_json = active_ids
        workflow.primary_node_execution_id = active_ids[0]
        workflow.current_stage = node_key
        self.bump_snapshot(workflow)
        await self.append_event(
            workflow,
            event_type="agent.node.started",
            public_summary=summary,
            public_payload={
                "node_execution_id": node.node_execution_id,
                "node_key": node_key,
                "node_type": node_type,
                "agent_role": agent_role,
            },
            node=node,
        )
        return node

    async def complete_node(
        self,
        workflow: LlmAgentWorkflowExecution,
        node: LlmAgentWorkflowNodeExecution,
        *,
        output: BaseModel,
        transition: NodeTransitionRead,
        evidence: NodeEvidenceRead,
        usage: NodeUsageRead,
        status_label: str,
        summary: str,
        commit_event: bool = True,
    ) -> LlmAgentWorkflowEvent:
        """Validate and persist a completed node with its transition and evidence event."""

        await self.ensure_workflow_running(workflow)
        definition = self._require_workflow_definition(workflow)
        definition.validate_transition(
            node_key=node.node_key,
            next_node_keys=transition.next_node_keys,
            skipped_node_keys=transition.skipped_node_keys,
        )
        output_json = validate_node_output(node.output_type, output.model_dump(mode="json"))
        serialized = json.dumps(output_json, ensure_ascii=False, separators=(",", ":"))
        if len(serialized.encode()) > MAX_NODE_RESULT_BYTES:
            raise InvalidRequestError("Agent node output exceeds the 64 KiB envelope limit")
        now = utc_now()
        node.status = AgentWorkflowNodeStatus.COMPLETED
        node.output_json = output_json
        node.transition_json = transition.model_dump(mode="json")
        node.evidence_json = evidence.model_dump(mode="json")
        node.usage_json = usage.model_dump(mode="json")
        node.completed_at = now
        node.duration_ms = max(
            0, int((_as_utc(now) - _as_utc(node.started_at)).total_seconds() * 1_000)
        )
        node.public_view_json = NodePublicViewRead(
            status_label=status_label,
            summary=summary,
            progress_current=node.node_sequence,
            progress_total=_progress_total(workflow, node.node_sequence),
        ).model_dump(mode="json")
        active_ids = [
            item
            for item in list(workflow.active_node_execution_ids_json or [])
            if item != node.node_execution_id
        ]
        workflow.active_node_execution_ids_json = active_ids
        workflow.primary_node_execution_id = active_ids[0] if active_ids else None
        workflow.current_stage = active_ids[0] if active_ids else node.node_key
        workflow.total_estimated_cost += usage.estimated_cost
        starting_budget = NodeBudgetRead.model_validate(node.budget_json or {})
        node.budget_json = (
            await self.build_node_budget(
                workflow,
                node_count=node.node_sequence,
                cost_used_before=starting_budget.cost_used_after,
            )
        ).model_dump(mode="json")
        self.bump_snapshot(workflow)
        return await self.append_event(
            workflow,
            event_type="agent.node.completed",
            public_summary=summary,
            public_payload={
                "node_execution_id": node.node_execution_id,
                "node_key": node.node_key,
                "output_type": node.output_type,
                "output_schema_version": node.output_schema_version,
                "node_result_path": (
                    f"/api/v1/agent-workflows/{workflow.workflow_execution_id}"
                    f"/nodes/{node.node_execution_id}"
                ),
            },
            node=node,
            commit_event=commit_event,
        )

    async def fail_node(
        self,
        workflow: LlmAgentWorkflowExecution,
        node: LlmAgentWorkflowNodeExecution,
        error: WorkflowErrorRead,
        *,
        cancelled: bool = False,
    ) -> None:
        """Persist a terminal failed, cancelled, or outcome-unknown node."""

        if node.status in {
            AgentWorkflowNodeStatus.COMPLETED,
            AgentWorkflowNodeStatus.FAILED,
            AgentWorkflowNodeStatus.CANCELLED,
            AgentWorkflowNodeStatus.OUTCOME_UNKNOWN,
        }:
            return
        evidence, usage = await self.node_attempt_evidence_and_usage(workflow, node)
        now = utc_now()
        if cancelled:
            node.status = AgentWorkflowNodeStatus.CANCELLED
        elif not error.outcome_is_known:
            node.status = AgentWorkflowNodeStatus.OUTCOME_UNKNOWN
        else:
            node.status = AgentWorkflowNodeStatus.FAILED
        node.error_code = error.error_code
        node.error_json = error.model_dump(mode="json")
        node.evidence_json = evidence.model_dump(mode="json")
        node.usage_json = usage.model_dump(mode="json")
        node.completed_at = now
        node.duration_ms = max(
            0, int((_as_utc(now) - _as_utc(node.started_at)).total_seconds() * 1_000)
        )
        status_label = (
            "Node cancelled"
            if cancelled
            else ("Node failed" if error.outcome_is_known else "Node outcome unknown")
        )
        node.public_view_json = NodePublicViewRead(
            status_label=status_label,
            summary=error.public_message,
            progress_current=node.node_sequence,
            progress_total=_progress_total(workflow, node.node_sequence),
        ).model_dump(mode="json")
        active_ids = [
            item
            for item in list(workflow.active_node_execution_ids_json or [])
            if item != node.node_execution_id
        ]
        workflow.active_node_execution_ids_json = active_ids
        workflow.primary_node_execution_id = active_ids[0] if active_ids else None
        workflow.total_estimated_cost += usage.estimated_cost
        starting_budget = NodeBudgetRead.model_validate(node.budget_json or {})
        node.budget_json = (
            await self.build_node_budget(
                workflow,
                node_count=node.node_sequence,
                cost_used_before=starting_budget.cost_used_after,
            )
        ).model_dump(mode="json")
        self.bump_snapshot(workflow)
        event_suffix = (
            "cancelled"
            if node.status == AgentWorkflowNodeStatus.CANCELLED
            else "outcome_unknown"
            if node.status == AgentWorkflowNodeStatus.OUTCOME_UNKNOWN
            else "failed"
        )
        await self.append_event(
            workflow,
            event_type=f"agent.node.{event_suffix}",
            public_summary=error.public_message,
            public_payload={
                "node_execution_id": node.node_execution_id,
                "node_key": node.node_key,
                "error": error.model_dump(mode="json"),
                "node_result_path": (
                    f"/api/v1/agent-workflows/{workflow.workflow_execution_id}"
                    f"/nodes/{node.node_execution_id}"
                ),
            },
            node=node,
        )

    async def complete_workflow(
        self,
        workflow: LlmAgentWorkflowExecution,
        run: LlmRun,
        *,
        completion_node: LlmAgentWorkflowNodeExecution,
        completion_output: BaseModel,
        transition: NodeTransitionRead,
        evidence: NodeEvidenceRead,
        usage: NodeUsageRead,
        status_label: str,
        summary: str,
    ) -> None:
        """Commit the completion node, Workflow, Run, and both events as one transaction."""

        if run.run_id != workflow.run_id:
            raise InvalidRequestError("Agent workflow completion Run does not own the workflow")
        node_event = await self.complete_node(
            workflow,
            completion_node,
            output=completion_output,
            transition=transition,
            evidence=evidence,
            usage=usage,
            status_label=status_label,
            summary=summary,
            commit_event=False,
        )
        now = utc_now()
        workflow.status = AgentWorkflowStatus.COMPLETED
        workflow.current_stage = "workflow_completion"
        workflow.primary_node_execution_id = None
        workflow.active_node_execution_ids_json = []
        workflow.completed_at = now
        run.status = RunStatus.COMPLETED
        run.completed_at = now
        workflow.reserved_estimated_cost = Decimal("0")
        self.bump_snapshot(workflow)
        workflow_event = await self.append_event(
            workflow,
            event_type="agent.workflow.completed",
            public_summary="Agent workflow completed",
            public_payload={
                "completion_node_execution_id": completion_node.node_execution_id,
                "result_path": (f"/api/v1/agent-workflows/{workflow.workflow_execution_id}/result"),
            },
            commit_event=False,
        )
        await self.db_session.commit()
        await self._notify_committed_events([node_event, workflow_event])

    async def fail_workflow(
        self,
        workflow: LlmAgentWorkflowExecution,
        *,
        error: WorkflowErrorRead,
    ) -> None:
        """Finalize unfinished child facts and persist a failed or unknown workflow result."""

        await self.db_session.refresh(workflow)
        if workflow.status in _TERMINAL_WORKFLOW_STATUSES:
            return
        await self._finalize_active_nodes(workflow, error=error, cancelled=False)
        await self._finalize_child_execution_facts(workflow, cancelled=False)
        await self.db_session.refresh(workflow)
        now = utc_now()
        workflow.status = (
            AgentWorkflowStatus.FAILED
            if error.outcome_is_known
            else AgentWorkflowStatus.OUTCOME_UNKNOWN
        )
        workflow.error_code = error.error_code
        workflow.error_json = error.model_dump(mode="json")
        workflow.primary_node_execution_id = None
        workflow.active_node_execution_ids_json = []
        workflow.completed_at = now
        workflow.reserved_estimated_cost = Decimal("0")
        run = await self.require_run(workflow.run_id)
        run.status = RunStatus.FAILED
        run.completed_at = now
        self.bump_snapshot(workflow)
        event_suffix = "failed" if error.outcome_is_known else "outcome_unknown"
        await self.append_event(
            workflow,
            event_type=f"agent.workflow.{event_suffix}",
            public_summary=error.public_message,
            public_payload={"error": error.model_dump(mode="json")},
        )

    async def cancel_workflow(
        self,
        workflow: LlmAgentWorkflowExecution,
        *,
        error: WorkflowErrorRead,
    ) -> None:
        """Finalize unfinished child facts and persist request cancellation once."""

        await self.db_session.refresh(workflow)
        if workflow.status in _TERMINAL_WORKFLOW_STATUSES:
            return
        await self._finalize_active_nodes(workflow, error=error, cancelled=True)
        await self._finalize_child_execution_facts(workflow, cancelled=True)
        await self.db_session.refresh(workflow)
        now = utc_now()
        workflow.status = AgentWorkflowStatus.CANCELLED
        workflow.primary_node_execution_id = None
        workflow.active_node_execution_ids_json = []
        workflow.completed_at = now
        workflow.reserved_estimated_cost = Decimal("0")
        run = await self.require_run(workflow.run_id)
        run.status = RunStatus.CANCELLED
        run.completed_at = now
        self.bump_snapshot(workflow)
        await self.append_event(
            workflow,
            event_type="agent.workflow.cancelled",
            public_summary="Agent workflow was cancelled",
            public_payload={},
        )

    async def _finalize_active_nodes(
        self,
        workflow: LlmAgentWorkflowExecution,
        *,
        error: WorkflowErrorRead,
        cancelled: bool,
    ) -> None:
        """Finalize every running node before recording a terminal workflow state."""

        active_nodes = list(
            (
                await self.db_session.scalars(
                    select(LlmAgentWorkflowNodeExecution).where(
                        LlmAgentWorkflowNodeExecution.workflow_execution_id
                        == workflow.workflow_execution_id,
                        LlmAgentWorkflowNodeExecution.status == AgentWorkflowNodeStatus.RUNNING,
                    )
                )
            ).all()
        )
        for active_node in active_nodes:
            await self.fail_node(
                workflow,
                active_node,
                error,
                cancelled=cancelled,
            )

    async def _finalize_child_execution_facts(
        self,
        workflow: LlmAgentWorkflowExecution,
        *,
        cancelled: bool,
    ) -> None:
        """Finalize unfinished Tasks, Agent Runs, and Turns owned by one workflow."""

        agent_runs = list(
            (
                await self.db_session.scalars(
                    select(LlmAgentRun).where(
                        LlmAgentRun.workflow_execution_id == workflow.workflow_execution_id
                    )
                )
            ).all()
        )
        now = utc_now()
        task_terminal_status = TaskStatus.CANCELLED if cancelled else TaskStatus.FAILED
        agent_terminal_status = AgentRunStatus.CANCELLED if cancelled else AgentRunStatus.FAILED
        for agent_run in agent_runs:
            if agent_run.status not in _TERMINAL_AGENT_RUN_STATUSES:
                agent_run.status = agent_terminal_status
                agent_run.completed_at = now
            task = await self.db_session.get(LlmTask, agent_run.task_id)
            if task is not None and task.status not in _TERMINAL_TASK_STATUSES:
                task.status = task_terminal_status
                task.completed_at = now
            turns = list(
                (
                    await self.db_session.scalars(
                        select(LlmAgentTurn).where(
                            LlmAgentTurn.agent_run_id == agent_run.agent_run_id,
                            LlmAgentTurn.status == AgentTurnStatus.STARTED,
                        )
                    )
                ).all()
            )
            for turn in turns:
                if turn.node_execution_id is not None:
                    node = await self.db_session.get(
                        LlmAgentWorkflowNodeExecution,
                        turn.node_execution_id,
                    )
                    if node is not None:
                        evidence, _ = await self.node_attempt_evidence_and_usage(
                            workflow,
                            node,
                        )
                        if evidence.model_attempt_ids:
                            turn.model_attempt_id = evidence.model_attempt_ids[0]
                turn.status = AgentTurnStatus.FAILED
                turn.completed_at = now
        await self.db_session.commit()

    async def node_attempt_evidence_and_usage(
        self,
        workflow: LlmAgentWorkflowExecution,
        node: LlmAgentWorkflowNodeExecution,
    ) -> tuple[NodeEvidenceRead, NodeUsageRead]:
        """Recover one persisted model Attempt when post-call node handling fails."""

        request_key = f"agent:{workflow.workflow_execution_id}:{node.node_sequence}"
        attempt = await self.db_session.scalar(
            select(LlmModelAttempt).where(LlmModelAttempt.request_key == request_key)
        )
        if attempt is None:
            return NodeEvidenceRead(), NodeUsageRead()
        return (
            NodeEvidenceRead(model_attempt_ids=[attempt.attempt_id]),
            NodeUsageRead(
                input_tokens=attempt.input_tokens or 0,
                output_tokens=attempt.output_tokens or 0,
                cached_tokens=attempt.cached_tokens or 0,
                estimated_cost=attempt.estimated_cost or Decimal("0"),
                model_call_count=1,
                tool_call_count=0,
            ),
        )

    async def node_has_outcome_unknown_attempt(
        self,
        workflow: LlmAgentWorkflowExecution,
        node: LlmAgentWorkflowNodeExecution,
    ) -> bool:
        """Return whether cancellation left this node's Provider Attempt indeterminate."""

        request_key = f"agent:{workflow.workflow_execution_id}:{node.node_sequence}"
        attempt_status = await self.db_session.scalar(
            select(LlmModelAttempt.status).where(LlmModelAttempt.request_key == request_key)
        )
        return attempt_status == AttemptStatus.OUTCOME_UNKNOWN

    async def workflow_has_outcome_unknown_attempt(
        self,
        workflow: LlmAgentWorkflowExecution,
    ) -> bool:
        """Return whether any Provider call in this workflow has an unknown outcome."""

        attempt_id = await self.db_session.scalar(
            select(LlmModelAttempt.attempt_id).where(
                LlmModelAttempt.run_id == workflow.run_id,
                LlmModelAttempt.request_key.like(f"agent:{workflow.workflow_execution_id}:%"),
                LlmModelAttempt.status == AttemptStatus.OUTCOME_UNKNOWN,
            )
        )
        return attempt_id is not None

    async def start_task_and_agent(self, task: LlmTask, agent_run: LlmAgentRun) -> None:
        """Start one worker only after every persisted dependency Task completes."""

        dependency_task_ids = list(
            (
                await self.db_session.scalars(
                    select(LlmTaskDependency.depends_on_task_id).where(
                        LlmTaskDependency.task_id == task.task_id
                    )
                )
            ).all()
        )
        for dependency_task_id in dependency_task_ids:
            dependency_task = await self.db_session.get(LlmTask, dependency_task_id)
            if dependency_task is None or dependency_task.status != TaskStatus.COMPLETED:
                raise ResourceConflictError(
                    "Agent Task cannot start before all dependency Tasks complete"
                )
        now = utc_now()
        task.status = TaskStatus.RUNNING
        task.current_attempt = 1
        task.started_at = now
        agent_run.status = AgentRunStatus.RUNNING
        agent_run.started_at = now
        await self.db_session.commit()

    async def complete_task_and_agent(self, task: LlmTask, agent_run: LlmAgentRun) -> None:
        """Mark one Task and Agent Run complete only after its Handoff is durable."""

        now = utc_now()
        task.status = TaskStatus.COMPLETED
        task.completed_at = now
        agent_run.status = AgentRunStatus.COMPLETED
        agent_run.completed_at = now
        await self.db_session.commit()

    async def start_agent_turn(
        self,
        agent_run: LlmAgentRun,
        node: LlmAgentWorkflowNodeExecution,
    ) -> LlmAgentTurn:
        """Create one ordered model Turn and advance the Agent Run sequence."""

        agent_run.current_turn_sequence += 1
        turn = LlmAgentTurn(
            agent_turn_id=new_id(),
            node_execution_id=node.node_execution_id,
            agent_run_id=agent_run.agent_run_id,
            turn_sequence=agent_run.current_turn_sequence,
            turn_type=AgentTurnType.MODEL,
            status=AgentTurnStatus.STARTED,
            started_at=utc_now(),
        )
        self.db_session.add(turn)
        await self.db_session.commit()
        return turn

    async def complete_agent_turn(
        self,
        turn: LlmAgentTurn,
        agent_run: LlmAgentRun,
        model_attempt_id: str,
    ) -> None:
        """Link one completed Model Attempt to its Agent Turn and persist completion."""

        attempt = await self.db_session.get(LlmModelAttempt, model_attempt_id)
        if (
            attempt is None
            or attempt.run_id != agent_run.run_id
            or attempt.task_id != agent_run.task_id
            or attempt.status != AttemptStatus.COMPLETED
            or attempt.provider != agent_run.provider
            or attempt.model != agent_run.model
        ):
            raise InvalidRequestError(
                "Completed Model Attempt does not match the Agent Run Task and binding"
            )
        turn.model_attempt_id = model_attempt_id
        turn.status = AgentTurnStatus.COMPLETED
        turn.completed_at = utc_now()
        await self.db_session.commit()

    async def save_final_message(
        self,
        run: LlmRun,
        output: FinalSynthesisModelOutput,
    ) -> str | None:
        """Stage a final Assistant Message for the final-node event transaction."""

        if run.session_id is None:
            return None
        message = await create_message(
            self.db_session,
            run.session_id,
            MessageCreate(
                role=MessageRole.ASSISTANT,
                content_text=output.final_text,
                run_id=run.run_id,
                metadata_json={
                    "source": "agent_workflow",
                    "schema_version": "final_synthesis_output.v1",
                },
            ),
            commit=False,
        )
        return message.message_id

    async def node_id_by_key(self, workflow_execution_id: str, node_key: str) -> str:
        """Return a previously committed node identity by its unique first-pass key."""

        node_id = await self.db_session.scalar(
            select(LlmAgentWorkflowNodeExecution.node_execution_id).where(
                LlmAgentWorkflowNodeExecution.workflow_execution_id == workflow_execution_id,
                LlmAgentWorkflowNodeExecution.node_key == node_key,
            )
        )
        if node_id is None:
            raise RuntimeError(f"Required workflow node {node_key} is missing")
        return node_id

    async def transport_attempt_count(self, model_attempt_id: str) -> int:
        """Return physical Provider call count from the durable logical Attempt."""

        attempt = await self.db_session.get(LlmModelAttempt, model_attempt_id)
        return (attempt.retry_count + 1) if attempt is not None else 0

    async def append_event(
        self,
        workflow: LlmAgentWorkflowExecution,
        *,
        event_type: str,
        public_summary: str,
        public_payload: dict,
        node: LlmAgentWorkflowNodeExecution | None = None,
        commit_event: bool = True,
    ) -> LlmAgentWorkflowEvent:
        """Stage an ordered event and optionally commit before notifying live observers."""

        workflow.event_count += 1
        event = LlmAgentWorkflowEvent(
            event_id=new_id(),
            workflow_execution_id=workflow.workflow_execution_id,
            run_id=workflow.run_id,
            node_execution_id=node.node_execution_id if node else None,
            event_sequence=workflow.event_count,
            event_type=event_type,
            workflow_status=workflow.status.value,
            node_status=node.status.value if node else None,
            public_payload_json=public_payload,
            occurred_at=utc_now(),
            public_summary=public_summary,
            trace_id=workflow.trace_id,
        )
        self.db_session.add(event)
        if commit_event:
            await self.db_session.commit()
            await self._notify_committed_events([event])
        return event

    async def _notify_committed_events(
        self,
        events: list[LlmAgentWorkflowEvent],
    ) -> None:
        """Notify the execution observer in event-sequence order after one successful commit."""

        if self.event_sink is None:
            return
        for event in events:
            durable_event = AgentWorkflowEventRead(
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
            await self.event_sink(durable_event)

    async def reserve_model_call(
        self,
        workflow: LlmAgentWorkflowExecution,
        *,
        reserved_estimated_cost: Decimal | None,
        has_explicit_output_token_bound: bool = True,
    ) -> None:
        """Reserve one call and distinguish a missing output bound from missing pricing."""

        reservation_cost = reserved_estimated_cost or Decimal("0")
        priced_budget_condition = select(LlmRun.run_id).where(
            LlmRun.run_id == LlmAgentWorkflowExecution.run_id,
            or_(
                LlmRun.budget_limit.is_(None),
                (
                    reserved_estimated_cost is not None
                    and LlmRun.cost_used
                    + LlmAgentWorkflowExecution.reserved_estimated_cost
                    + reservation_cost
                    <= LlmRun.budget_limit
                ),
            ),
        )
        reservation_update = await self.db_session.execute(
            update(LlmAgentWorkflowExecution)
            .where(
                LlmAgentWorkflowExecution.workflow_execution_id == workflow.workflow_execution_id,
                LlmAgentWorkflowExecution.status == AgentWorkflowStatus.RUNNING,
                LlmAgentWorkflowExecution.model_call_count
                < LlmAgentWorkflowExecution.max_model_calls,
                priced_budget_condition.exists(),
            )
            .values(
                model_call_count=LlmAgentWorkflowExecution.model_call_count + 1,
                reserved_estimated_cost=(
                    LlmAgentWorkflowExecution.reserved_estimated_cost + reservation_cost
                ),
                version=LlmAgentWorkflowExecution.version + 1,
                snapshot_version=LlmAgentWorkflowExecution.snapshot_version + 1,
            )
        )
        if reservation_update.rowcount == 1:
            await self.db_session.commit()
            await self.db_session.refresh(workflow)
            return

        await self.db_session.rollback()
        await self.db_session.refresh(workflow)
        if workflow.status != AgentWorkflowStatus.RUNNING:
            raise WorkflowExecutionStopped
        if workflow.model_call_count >= workflow.max_model_calls:
            raise InvalidRequestError("Agent workflow reached its model-call limit")
        run = await self.require_run(workflow.run_id)
        if run.budget_limit is not None and reserved_estimated_cost is None:
            if not has_explicit_output_token_bound:
                raise InvalidRequestError(
                    "Run budget requires explicit max_output_tokens before provider call"
                )
            raise InvalidRequestError(
                "Run budget requires a configured model price before provider call"
            )
        raise InvalidRequestError("Run budget cannot cover the reserved model call cost")

    async def release_model_cost_reservation(
        self,
        workflow: LlmAgentWorkflowExecution,
        *,
        reserved_estimated_cost: Decimal | None,
    ) -> None:
        """Atomically remove one in-flight call reservation without changing actual cost."""

        if reserved_estimated_cost is None or reserved_estimated_cost == 0:
            return
        await self.db_session.execute(
            update(LlmAgentWorkflowExecution)
            .where(
                LlmAgentWorkflowExecution.workflow_execution_id == workflow.workflow_execution_id
            )
            .values(
                reserved_estimated_cost=case(
                    (
                        LlmAgentWorkflowExecution.reserved_estimated_cost
                        >= reserved_estimated_cost,
                        LlmAgentWorkflowExecution.reserved_estimated_cost - reserved_estimated_cost,
                    ),
                    else_=Decimal("0"),
                ),
                version=LlmAgentWorkflowExecution.version + 1,
                snapshot_version=LlmAgentWorkflowExecution.snapshot_version + 1,
            )
        )
        await self.db_session.commit()
        await self.db_session.refresh(workflow)

    async def ensure_workflow_running(
        self,
        workflow: LlmAgentWorkflowExecution,
    ) -> None:
        """Reject work after terminal state or exhaustion of the synchronous deadline."""

        await self.db_session.refresh(workflow)
        if workflow.status != AgentWorkflowStatus.RUNNING:
            raise WorkflowExecutionStopped
        if self.remaining_wall_time_ms(workflow) <= 0:
            raise InvalidRequestError("Agent workflow wall-time limit is exhausted")

    def remaining_wall_time_ms(self, workflow: LlmAgentWorkflowExecution) -> int:
        """Return the non-negative wall-clock allowance remaining for this workflow."""

        elapsed_ms = max(
            0,
            int(
                (_as_utc(utc_now()) - _as_utc(workflow.started_at or utc_now())).total_seconds()
                * 1_000
            ),
        )
        return max(0, workflow.wall_time_limit_ms - elapsed_ms)

    async def build_node_budget(
        self,
        workflow: LlmAgentWorkflowExecution,
        *,
        node_count: int,
        cost_used_before: Decimal | None = None,
    ) -> NodeBudgetRead:
        """Calculate a node budget projection from current durable Run and workflow facts."""

        run = await self.require_run(workflow.run_id)
        remaining_cost = None
        if run.budget_limit is not None:
            remaining_cost = max(
                Decimal("0"),
                run.budget_limit - run.cost_used - workflow.reserved_estimated_cost,
            )
        return NodeBudgetRead(
            cost_limit=run.budget_limit,
            cost_used_before=(run.cost_used if cost_used_before is None else cost_used_before),
            cost_used_after=run.cost_used,
            remaining_cost=remaining_cost,
            reserved_estimated_cost=workflow.reserved_estimated_cost,
            remaining_model_calls=max(0, workflow.max_model_calls - workflow.model_call_count),
            remaining_nodes=max(0, workflow.max_nodes - node_count),
            remaining_wall_time_ms=self.remaining_wall_time_ms(workflow),
        )

    async def require_run(self, run_id: str) -> LlmRun:
        """Return the owning Run or raise the stable resource-not-found error."""

        run = await self.db_session.get(LlmRun, run_id)
        if run is None:
            raise ResourceNotFoundError("Run")
        return run

    def bump_snapshot(self, workflow: LlmAgentWorkflowExecution) -> None:
        """Advance optimistic workflow and result-snapshot versions together."""

        workflow.version += 1
        workflow.snapshot_version += 1

    def _require_workflow_definition(
        self,
        workflow: LlmAgentWorkflowExecution,
    ) -> AgentWorkflowDefinition:
        """Return the immutable definition selected when this workflow was created."""

        return self.workflow_definitions.require_definition(
            workflow.workflow_name,
            workflow.workflow_version,
            workflow.execution_profile,
        )


def _as_utc(value: datetime) -> datetime:
    """Treat SQLite-naive persisted UTC values as UTC for elapsed calculations."""

    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _progress_total(
    workflow: LlmAgentWorkflowExecution,
    node_sequence: int,
) -> int:
    """Return a display bound that never falls below the durable node sequence."""

    return min(workflow.max_nodes, max(11, node_sequence))
