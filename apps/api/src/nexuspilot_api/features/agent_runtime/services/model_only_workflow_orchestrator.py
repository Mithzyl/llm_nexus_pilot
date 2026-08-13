"""Decide the node order and review branches for the model_only_v1 workflow."""

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from nexuspilot_models.errors import ModelProviderError
from nexuspilot_models.pricing import PriceCatalog
from nexuspilot_models.registry import ProviderRegistry
from pydantic import BaseModel
from sqlalchemy import inspect as inspect_orm
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from nexuspilot_api.core.errors import InvalidRequestError
from nexuspilot_api.features.agent_runtime.schemas.agent_workflows import (
    AgentDispatchOutput,
    AgentHandoffOutput,
    AgentModelBinding,
    AgentModelExecutionOutput,
    AgentWorkerModelOutput,
    AgentWorkflowCreate,
    ContextAssemblyOutput,
    ControllerPlanOutput,
    DeterministicVerificationOutput,
    FinalSynthesisModelOutput,
    FinalSynthesisOutput,
    IndependentReviewOutput,
    NodeBudgetRead,
    NodeEvidenceRead,
    NodeTransitionRead,
    NodeUsageRead,
    PlanValidationOutput,
    RequestIntakeOutput,
    ReviewerModelOutput,
    VerificationCheck,
    WorkflowCompletionOutput,
    WorkflowErrorRead,
)
from nexuspilot_api.features.agent_runtime.services.agent_model_node_service import (
    AgentModelNodeService,
)
from nexuspilot_api.features.agent_runtime.services.workflow_definition_registry import (
    DEFAULT_WORKFLOW_DEFINITION_REGISTRY,
    WorkflowDefinitionRegistry,
)
from nexuspilot_api.features.agent_runtime.services.workflow_execution_state_service import (
    WorkflowExecutionStateService,
    WorkflowExecutionStopped,
)
from nexuspilot_api.features.agent_runtime.services.workflow_policy import validate_controller_plan
from nexuspilot_api.features.memory.schemas.collaboration_memory import AgentHandoffCreate
from nexuspilot_api.features.memory.services.collaboration_memory_service import submit_handoff
from nexuspilot_api.infrastructure.object_storage import ObjectStorage
from nexuspilot_api.models import (
    AgentRunStatus,
    AttemptStatus,
    EvaluationStatus,
    EvaluationVerdict,
    HandoffStatus,
    LlmAgentHandoff,
    LlmAgentRun,
    LlmAgentTurn,
    LlmAgentWorkflowEvent,
    LlmAgentWorkflowExecution,
    LlmAgentWorkflowNodeExecution,
    LlmModelAttempt,
    LlmRun,
    LlmTask,
    LlmTaskDependency,
    LlmTaskEvaluation,
    TaskStatus,
    new_id,
)
from nexuspilot_api.models.base import utc_now
from nexuspilot_api.schemas.responses import ResponsesResult
from nexuspilot_api.services.model_response_service import ModelInvocationService


@dataclass(frozen=True)
class WorkerExecutionRecord:
    """Keep one worker's ownership and evidence joined without positional list matching."""

    task_key: str
    task_id: str
    agent_run_id: str
    worker_node_execution_id: str
    handoff_node_execution_id: str
    model_attempt_id: str
    worker_output: AgentWorkerModelOutput
    handoff: AgentHandoffOutput


@dataclass(frozen=True)
class PreparedWorkerExecution:
    """Identify one started Worker node and the exact Provider input it must use."""

    task_key: str
    task_id: str
    agent_run_id: str
    agent_turn_id: str
    node_execution_id: str
    binding: AgentModelBinding
    agent_role: str
    model_input: dict[str, Any]
    timeout_seconds: int


@dataclass(frozen=True)
class WorkerModelCallOutcome:
    """Carry either one validated Worker response or its original execution failure."""

    prepared: PreparedWorkerExecution
    worker_output: AgentWorkerModelOutput | None = None
    response: ResponsesResult | None = None
    error: Exception | None = None


class WorkerExecutionFailure(Exception):
    """Associate a Worker Provider failure with the node that must be finalized."""

    def __init__(self, node_execution_id: str, error: Exception) -> None:
        """Preserve the failed node identity and the original public error source."""

        super().__init__(str(error))
        self.node_execution_id = node_execution_id
        self.original_error = error


class ModelOnlyWorkflowOrchestrator:
    """Run model-only nodes in order while preserving Task, Attempt, and Handoff facts."""

    def __init__(
        self,
        *,
        registry: ProviderRegistry,
        prices: PriceCatalog,
        db_session: AsyncSession,
        db_session_factory: async_sessionmaker[AsyncSession],
        storage: ObjectStorage,
        workflow_definitions: WorkflowDefinitionRegistry | None = None,
    ) -> None:
        """Bind dependencies used while executing one model-only workflow."""

        self.db_session = db_session
        self.db_session_factory = db_session_factory
        self.storage = storage
        selected_workflow_definitions = workflow_definitions or DEFAULT_WORKFLOW_DEFINITION_REGISTRY
        self.registry = registry
        self.prices = prices
        self.workflow_definitions = selected_workflow_definitions
        self.model_service = ModelInvocationService(
            registry=registry,
            prices=prices,
            db_session=db_session,
            storage=storage,
        )
        self.workflow_state = WorkflowExecutionStateService(
            db_session=db_session,
            workflow_definitions=selected_workflow_definitions,
        )
        self.model_node_service = AgentModelNodeService(
            model_invocation_service=self.model_service,
            workflow_state=self.workflow_state,
        )

    async def execute(
        self,
        workflow: LlmAgentWorkflowExecution,
        payload: AgentWorkflowCreate,
    ) -> None:
        """Run every model_only_v1 node in order and persist terminal failure evidence."""

        current_node: LlmAgentWorkflowNodeExecution | None = None
        try:
            run = await self._require_run(workflow.run_id)
            intake = RequestIntakeOutput(
                normalized_objective=run.user_request.strip(),
                request_type=run.run_type,
                complexity="complex",
                requires_decomposition=True,
                constraints=[
                    "model_only_v1 cannot execute tools, files, shell, Git, or network actions"
                ],
                acceptance_criteria=["Return an evidence-bounded final answer"],
                explicit_assumptions=[],
                clarification_questions=[],
                required_capabilities=["model_generation"],
                unavailable_capabilities=[],
                requested_output_format="text",
                language="auto",
            )
            intake_node = await self._run_deterministic_node(
                workflow,
                node_key="request_intake",
                node_type="deterministic",
                output_type="request_intake",
                output=intake,
                input_values={"run_id": run.run_id},
                transition=NodeTransitionRead(
                    selected_transition="assemble_context",
                    next_node_keys=["context_assembly"],
                    condition_summary="The Run request is valid and bounded",
                ),
                status_label="Request accepted",
                summary="Normalized the Run objective and capability boundary",
            )

            context = ContextAssemblyOutput(
                included_run_id=run.run_id,
                included_message_ids=[],
                included_artifact_ids=[],
                included_handoff_ids=[],
                excluded_sources=[],
                input_character_count=len(run.user_request),
                is_truncated=False,
                memory_packet_id=None,
            )
            context_node = await self._run_deterministic_node(
                workflow,
                node_key="context_assembly",
                node_type="context",
                output_type="context_assembly",
                output=context,
                input_values={"run_id": run.run_id, "objective": run.user_request},
                source_node_ids=[intake_node.node_execution_id],
                transition=NodeTransitionRead(
                    selected_transition="plan",
                    next_node_keys=["controller_planning"],
                    condition_summary="Explicit Run content is ready for Controller planning",
                ),
                status_label="Context assembled",
                summary="Prepared explicit Run content without Memory injection",
            )

            controller_binding = payload.role_bindings["controller"]
            current_node = await self._start_node(
                workflow,
                node_key="controller_planning",
                node_type="model",
                output_type="controller_plan",
                input_values={"objective": run.user_request, "profile": payload.execution_profile},
                source_node_ids=[context_node.node_execution_id],
                agent_role="controller",
                status_label="Controller planning",
                summary="Controller is creating a bounded Agent task plan",
            )
            plan, plan_response = await self._call_typed_model(
                workflow,
                current_node,
                binding=controller_binding,
                role="controller",
                purpose="Create a small task plan for the supplied objective.",
                model_output=ControllerPlanOutput,
                model_input={
                    "objective": run.user_request,
                    "allowed_roles": sorted(
                        {"planner", "researcher"}.intersection(payload.role_bindings)
                    ),
                    "allowed_capabilities": ["model_generation", "text_analysis"],
                    "maximum_tasks": 8,
                    "review_policy": payload.review_policy,
                },
            )
            await self._complete_node(
                workflow,
                current_node,
                output=plan,
                transition=NodeTransitionRead(
                    selected_transition="validate_plan",
                    next_node_keys=["plan_validation"],
                    condition_summary="Controller returned a schema-valid plan",
                ),
                evidence=self._model_evidence(plan_response),
                usage=self._model_usage(plan_response),
                status_label="Plan created",
                summary="Controller created a typed task plan",
            )
            current_node = None

            validation = validate_controller_plan(plan, payload)
            validation_node = await self._run_deterministic_node(
                workflow,
                node_key="plan_validation",
                node_type="deterministic",
                output_type="plan_validation",
                output=validation,
                input_values=plan.model_dump(mode="json"),
                source_node_ids=[
                    await self._node_id_by_key(
                        workflow.workflow_execution_id, "controller_planning"
                    )
                ],
                transition=NodeTransitionRead(
                    selected_transition="dispatch" if validation.is_valid else "fail",
                    next_node_keys=["agent_dispatch"] if validation.is_valid else [],
                    skipped_node_keys=([] if validation.is_valid else ["agent_dispatch"]),
                    condition_summary=(
                        "Plan passed deterministic validation"
                        if validation.is_valid
                        else "Plan requested unavailable or invalid work"
                    ),
                ),
                status_label="Plan validated" if validation.is_valid else "Plan rejected",
                summary=(
                    "The Controller plan passed deterministic checks"
                    if validation.is_valid
                    else "The Controller plan cannot run under model_only_v1"
                ),
            )
            if not validation.is_valid:
                error_code = next(
                    (
                        check.error_code
                        for check in validation.checks
                        if check.status == "fail" and check.error_code is not None
                    ),
                    "agent_plan_invalid",
                )
                await self._fail_workflow(
                    workflow,
                    error=self._workflow_error(
                        error_code,
                        validation.rejected_plan_reason or "Controller plan is invalid.",
                    ),
                )
                raise WorkflowExecutionStopped

            dispatch, task_by_key, agent_run_by_task_key = await self._dispatch_plan(
                workflow,
                payload,
                plan,
                validation,
                parent_node_id=validation_node.node_execution_id,
            )
            dependency_keys_by_task_key: dict[str, list[str]] = {
                task_key: [] for task_key in validation.topological_task_order
            }
            for dependency in plan.dependencies:
                dependency_keys_by_task_key[dependency.task_key].append(
                    dependency.depends_on_task_key
                )
            worker_records_by_task_key = await self._execute_worker_task_groups(
                workflow,
                payload,
                plan,
                run,
                dispatch_node_execution_id=dispatch.node_execution_id,
                task_by_key=task_by_key,
                agent_run_by_task_key=agent_run_by_task_key,
                dependency_keys_by_task_key=dependency_keys_by_task_key,
                topological_task_order=validation.topological_task_order,
            )

            worker_records = [
                worker_records_by_task_key[task_key]
                for task_key in validation.topological_task_order
            ]

            (
                verification,
                evaluation_ids,
                verification_node_execution_id,
            ) = await self._verify_handoffs(
                workflow,
                worker_records,
                source_node_ids=[record.handoff_node_execution_id for record in worker_records],
            )
            review_output: IndependentReviewOutput | None = None
            review_node_execution_id: str | None = None
            should_review = payload.review_policy == "always" or (
                payload.review_policy == "on_verification_failure"
                and verification.verdict != "pass"
            )
            if should_review:
                review_output, review_node_execution_id = await self._run_reviewer(
                    workflow,
                    payload,
                    run,
                    worker_records,
                    verification,
                    evaluation_ids,
                    verification_node_execution_id=verification_node_execution_id,
                )
                evaluation_ids.extend(review_output.evaluation_ids)

            if verification.verdict != "pass":
                await self._fail_workflow(
                    workflow,
                    error=self._workflow_error(
                        "agent_verification_failed",
                        "Deterministic verification did not pass.",
                    ),
                )
                raise WorkflowExecutionStopped
            if review_output is not None and (
                review_output.verdict != "pass"
                or review_output.requires_replan
                or review_output.requires_retry
            ):
                await self._fail_workflow(
                    workflow,
                    error=self._workflow_error(
                        "agent_review_rejected",
                        "Independent review rejected the candidate results.",
                    ),
                )
                raise WorkflowExecutionStopped

            worker_result_inputs = [self._worker_record_input(record) for record in worker_records]
            handoff_outputs = [record.handoff for record in worker_records]
            worker_agent_run_ids = [record.agent_run_id for record in worker_records]

            current_node = await self._start_node(
                workflow,
                node_key="final_synthesis",
                node_type="model",
                output_type="final_synthesis",
                input_values={
                    "objective": run.user_request,
                    "worker_results": worker_result_inputs,
                    "handoffs": [item.model_dump(mode="json") for item in handoff_outputs],
                    "verification": verification.model_dump(mode="json"),
                    "review": review_output.model_dump(mode="json") if review_output else None,
                },
                source_node_ids=[
                    *[record.handoff_node_execution_id for record in worker_records],
                    verification_node_execution_id,
                    *([review_node_execution_id] if review_node_execution_id else []),
                ],
                handoff_ids=[item.agent_handoff_id for item in handoff_outputs],
                agent_role="controller",
                status_label="Synthesizing final answer",
                summary="Controller is combining verified Agent results",
            )
            final_model_output, final_response = await self._call_typed_model(
                workflow,
                current_node,
                binding=controller_binding,
                role="controller",
                purpose=(
                    "Synthesize the final answer and distinguish verified from unresolved items."
                ),
                model_output=FinalSynthesisModelOutput,
                model_input={
                    "objective": run.user_request,
                    "worker_results": worker_result_inputs,
                    "handoffs": [item.model_dump(mode="json") for item in handoff_outputs],
                    "verification": verification.model_dump(mode="json"),
                    "review": review_output.model_dump(mode="json") if review_output else None,
                },
            )
            assistant_message_id = await self._save_final_message(run, final_model_output)
            final_output = FinalSynthesisOutput(
                **final_model_output.model_dump(),
                source_agent_run_ids=worker_agent_run_ids,
                source_handoff_ids=[item.agent_handoff_id for item in handoff_outputs],
                source_artifact_ids=[],
                source_evaluation_ids=evaluation_ids,
                assistant_message_id=assistant_message_id,
            )
            await self._complete_node(
                workflow,
                current_node,
                output=final_output,
                transition=NodeTransitionRead(
                    selected_transition="complete",
                    next_node_keys=["workflow_completion"],
                    condition_summary="Final answer passed its typed output contract",
                ),
                evidence=self._model_evidence(
                    final_response,
                    evaluation_ids=evaluation_ids,
                    handoff_ids=[item.agent_handoff_id for item in handoff_outputs],
                ),
                usage=self._model_usage(final_response),
                status_label="Final answer ready",
                summary="Controller synthesized the verified workflow result",
            )
            final_node_id = current_node.node_execution_id
            current_node = None
            await self._complete_workflow(
                workflow,
                run,
                final_node_id=final_node_id,
                task_ids=[task.task_id for task in task_by_key.values()]
                + ([review_output.reviewer_task_id] if review_output else []),
                agent_run_ids=worker_agent_run_ids
                + ([review_output.reviewer_agent_run_id] if review_output else []),
                handoff_ids=[item.agent_handoff_id for item in handoff_outputs],
                evaluation_ids=evaluation_ids,
            )
        except WorkflowExecutionStopped:
            return
        except asyncio.CancelledError:
            provider_outcome_is_unknown = (
                current_node is not None
                and await self._node_has_outcome_unknown_attempt(workflow, current_node)
            ) or await self.workflow_state.workflow_has_outcome_unknown_attempt(workflow)
            if provider_outcome_is_unknown:
                await self._fail_workflow(
                    workflow,
                    error=self._workflow_error(
                        "agent_provider_outcome_unknown",
                        (
                            "Workflow was cancelled after entering the provider adapter; "
                            "provider dispatch and billing outcome cannot be confirmed."
                        ),
                        is_retryable=False,
                        outcome_is_known=False,
                    ),
                )
            else:
                if current_node is not None:
                    await self._fail_node(
                        workflow,
                        current_node,
                        self._workflow_error(
                            "agent_workflow_cancelled", "Workflow request was cancelled."
                        ),
                        cancelled=True,
                    )
                await self._cancel_workflow(workflow)
            raise
        except Exception as exc:
            workflow_execution_id = workflow.workflow_execution_id
            current_node_execution_id = (
                exc.node_execution_id
                if isinstance(exc, WorkerExecutionFailure)
                else str(inspect_orm(current_node).identity[0])
                if current_node is not None
                else None
            )
            await self.db_session.rollback()
            workflow = await self.db_session.get(
                LlmAgentWorkflowExecution,
                workflow_execution_id,
            )
            if workflow is None:
                raise
            current_node = (
                await self.db_session.get(
                    LlmAgentWorkflowNodeExecution,
                    current_node_execution_id,
                )
                if current_node_execution_id is not None
                else None
            )
            error_source = exc.original_error if isinstance(exc, WorkerExecutionFailure) else exc
            error = self._public_error_from_exception(error_source)
            if current_node is not None:
                await self._fail_node(workflow, current_node, error)
            await self._fail_workflow(workflow, error=error)

    async def _run_deterministic_node(
        self,
        workflow: LlmAgentWorkflowExecution,
        *,
        node_key: str,
        node_type: str,
        output_type: str,
        output: BaseModel,
        input_values: dict,
        transition: NodeTransitionRead,
        status_label: str,
        summary: str,
        source_node_ids: list[str] | None = None,
    ) -> LlmAgentWorkflowNodeExecution:
        """Persist one deterministic node start and validated completion."""

        node = await self._start_node(
            workflow,
            node_key=node_key,
            node_type=node_type,
            output_type=output_type,
            input_values=input_values,
            source_node_ids=source_node_ids,
            status_label=status_label,
            summary=summary,
        )
        await self._complete_node(
            workflow,
            node,
            output=output,
            transition=transition,
            evidence=NodeEvidenceRead(),
            usage=NodeUsageRead(),
            status_label=status_label,
            summary=summary,
        )
        return node

    async def _execute_worker_task_groups(
        self,
        workflow: LlmAgentWorkflowExecution,
        payload: AgentWorkflowCreate,
        plan: ControllerPlanOutput,
        run: LlmRun,
        *,
        dispatch_node_execution_id: str,
        task_by_key: dict[str, LlmTask],
        agent_run_by_task_key: dict[str, LlmAgentRun],
        dependency_keys_by_task_key: dict[str, list[str]],
        topological_task_order: list[str],
    ) -> dict[str, WorkerExecutionRecord]:
        """Run each dependency-ready Worker group with at most two independent sessions."""

        completion_criteria_by_task_key = {
            planned_task.task_key: planned_task.completion_criteria for planned_task in plan.tasks
        }
        worker_records_by_task_key: dict[str, WorkerExecutionRecord] = {}
        execution_groups = self._build_worker_execution_groups(
            topological_task_order,
            dependency_keys_by_task_key,
            payload.max_parallel_agents,
        )
        for task_keys in execution_groups:
            prepared_workers: list[PreparedWorkerExecution] = []
            for task_key in task_keys:
                dependency_records = [
                    worker_records_by_task_key[dependency_key]
                    for dependency_key in dependency_keys_by_task_key[task_key]
                ]
                prepared_workers.append(
                    await self._prepare_worker_execution(
                        workflow,
                        run,
                        task_key=task_key,
                        task=task_by_key[task_key],
                        agent_run=agent_run_by_task_key[task_key],
                        binding=payload.role_bindings[agent_run_by_task_key[task_key].agent_role],
                        completion_criteria=completion_criteria_by_task_key[task_key],
                        dependency_records=dependency_records,
                        dispatch_node_execution_id=dispatch_node_execution_id,
                    )
                )
            outcomes = await self._call_worker_group(
                workflow.workflow_execution_id,
                prepared_workers,
            )
            first_failure: WorkerModelCallOutcome | None = None
            for outcome in outcomes:
                if outcome.error is not None:
                    first_failure = first_failure or outcome
                    continue
                record = await self._finalize_worker_execution(workflow, outcome)
                worker_records_by_task_key[record.task_key] = record
            if first_failure is not None:
                if isinstance(first_failure.error, WorkflowExecutionStopped):
                    raise first_failure.error
                assert first_failure.error is not None
                raise WorkerExecutionFailure(
                    first_failure.prepared.node_execution_id,
                    first_failure.error,
                )
        return worker_records_by_task_key

    async def _prepare_worker_execution(
        self,
        workflow: LlmAgentWorkflowExecution,
        run: LlmRun,
        *,
        task_key: str,
        task: LlmTask,
        agent_run: LlmAgentRun,
        binding: AgentModelBinding,
        completion_criteria: list[str],
        dependency_records: list[WorkerExecutionRecord],
        dispatch_node_execution_id: str,
    ) -> PreparedWorkerExecution:
        """Commit one Worker's Task, Agent Run, node, and Turn before Provider entry."""

        await self._start_task_and_agent(task, agent_run)
        dependency_results = [self._worker_record_input(record) for record in dependency_records]
        node = await self._start_node(
            workflow,
            node_key=f"worker_execution.{task_key}",
            node_type="agent_model_execution",
            output_type="agent_model_execution",
            input_values={
                "objective": task.objective,
                "completion_criteria": completion_criteria,
                "dependency_handoff_ids": [
                    record.handoff.agent_handoff_id for record in dependency_records
                ],
            },
            source_node_ids=[dispatch_node_execution_id]
            + [record.handoff_node_execution_id for record in dependency_records],
            handoff_ids=[record.handoff.agent_handoff_id for record in dependency_records],
            task_id=task.task_id,
            agent_run_id=agent_run.agent_run_id,
            agent_role=agent_run.agent_role,
            status_label=f"{agent_run.agent_role} running",
            summary=f"{agent_run.agent_role} is analyzing explicit workflow input",
        )
        turn = await self._start_agent_turn(agent_run, node)
        return PreparedWorkerExecution(
            task_key=task_key,
            task_id=task.task_id,
            agent_run_id=agent_run.agent_run_id,
            agent_turn_id=turn.agent_turn_id,
            node_execution_id=node.node_execution_id,
            binding=binding,
            agent_role=agent_run.agent_role,
            model_input={
                "task": {"title": task.title, "objective": task.objective},
                "run_objective": run.user_request,
                "dependency_results": dependency_results,
            },
            timeout_seconds=task.timeout_seconds,
        )

    async def _call_worker_group(
        self,
        workflow_execution_id: str,
        prepared_workers: list[PreparedWorkerExecution],
    ) -> list[WorkerModelCallOutcome]:
        """Call dependency-free Worker models concurrently and retain every outcome."""

        outcomes_by_task_key: dict[str, WorkerModelCallOutcome] = {}

        async def collect_worker_outcome(prepared: PreparedWorkerExecution) -> None:
            """Capture one Worker exception without cancelling already-billable siblings."""

            try:
                worker_output, response = await self._call_worker_in_new_session(
                    workflow_execution_id,
                    prepared,
                )
                outcomes_by_task_key[prepared.task_key] = WorkerModelCallOutcome(
                    prepared=prepared,
                    worker_output=worker_output,
                    response=response,
                )
            except Exception as error:
                outcomes_by_task_key[prepared.task_key] = WorkerModelCallOutcome(
                    prepared=prepared,
                    error=error,
                )

        async with asyncio.TaskGroup() as task_group:
            for prepared in prepared_workers:
                task_group.create_task(collect_worker_outcome(prepared))
        return [outcomes_by_task_key[item.task_key] for item in prepared_workers]

    async def _call_worker_in_new_session(
        self,
        workflow_execution_id: str,
        prepared: PreparedWorkerExecution,
    ) -> tuple[AgentWorkerModelOutput, ResponsesResult]:
        """Execute one Worker Provider call with a database session not shared by siblings."""

        async with self.db_session_factory() as worker_session:
            workflow = await worker_session.get(
                LlmAgentWorkflowExecution,
                workflow_execution_id,
            )
            node = await worker_session.get(
                LlmAgentWorkflowNodeExecution,
                prepared.node_execution_id,
            )
            if workflow is None or node is None:
                raise RuntimeError("Prepared Worker execution facts are unavailable")
            state_service = WorkflowExecutionStateService(
                db_session=worker_session,
                workflow_definitions=self.workflow_definitions,
            )
            model_service = ModelInvocationService(
                registry=self.registry,
                prices=self.prices,
                db_session=worker_session,
                storage=self.storage,
            )
            model_node_service = AgentModelNodeService(
                model_invocation_service=model_service,
                workflow_state=state_service,
            )
            worker_output, response = await model_node_service.call_typed_model(
                workflow,
                node,
                binding=prepared.binding,
                role=prepared.agent_role,
                purpose=(
                    "Complete the assigned task and return an evidence-bounded handoff summary."
                ),
                model_output=AgentWorkerModelOutput,
                model_input=prepared.model_input,
                timeout_seconds=prepared.timeout_seconds,
            )
            return worker_output, response

    async def _finalize_worker_execution(
        self,
        workflow: LlmAgentWorkflowExecution,
        outcome: WorkerModelCallOutcome,
    ) -> WorkerExecutionRecord:
        """Persist a successful Worker Turn, node, Handoff, Task, and Agent Run in order."""

        prepared = outcome.prepared
        assert outcome.worker_output is not None
        assert outcome.response is not None
        await self.db_session.refresh(workflow)
        task = await self.db_session.get(LlmTask, prepared.task_id, populate_existing=True)
        agent_run = await self.db_session.get(
            LlmAgentRun,
            prepared.agent_run_id,
            populate_existing=True,
        )
        turn = await self.db_session.get(
            LlmAgentTurn,
            prepared.agent_turn_id,
            populate_existing=True,
        )
        node = await self.db_session.get(
            LlmAgentWorkflowNodeExecution,
            prepared.node_execution_id,
            populate_existing=True,
        )
        if task is None or agent_run is None or turn is None or node is None:
            raise RuntimeError("Successful Worker execution facts are unavailable")
        await self._complete_agent_turn(turn, agent_run, outcome.response.id)
        node.agent_turn_id = turn.agent_turn_id
        model_node_output = AgentModelExecutionOutput(
            agent_turn_id=turn.agent_turn_id,
            model_attempt_id=outcome.response.id,
            provider=outcome.response.provider,
            model=outcome.response.model,
            text_preview=(outcome.response.output_text or "")[:1_000] or None,
            structured_output=outcome.worker_output,
            requested_tool_calls=[],
            finish_reason=outcome.response.finish_reason,
            provider_request_id=outcome.response.provider_request_id,
            input_tokens=outcome.response.usage.input_tokens,
            output_tokens=outcome.response.usage.output_tokens,
            cached_tokens=outcome.response.usage.cached_tokens,
            estimated_cost=outcome.response.usage.estimated_cost,
            latency_ms=outcome.response.latency_ms,
            transport_attempt_count=await self._transport_attempt_count(outcome.response.id),
            capability_warnings=[],
        )
        await self._complete_node(
            workflow,
            node,
            output=model_node_output,
            transition=NodeTransitionRead(
                selected_transition="submit_handoff",
                next_node_keys=[f"handoff_submission.{prepared.task_key}"],
                condition_summary="Worker returned schema-valid model-only output",
            ),
            evidence=self._model_evidence(outcome.response),
            usage=self._model_usage(outcome.response),
            status_label="Agent task complete",
            summary=f"{agent_run.agent_role} completed the assigned analysis",
        )
        handoff_node = await self._start_node(
            workflow,
            node_key=f"handoff_submission.{prepared.task_key}",
            node_type="handoff",
            output_type="agent_handoff",
            input_values=outcome.worker_output.model_dump(mode="json"),
            source_node_ids=[node.node_execution_id],
            task_id=task.task_id,
            agent_run_id=agent_run.agent_run_id,
            agent_turn_id=turn.agent_turn_id,
            agent_role=agent_run.agent_role,
            status_label="Submitting handoff",
            summary="Persisting the Agent result as immutable collaboration evidence",
        )
        handoff_output = await self._submit_worker_handoff(
            workflow,
            task,
            agent_run,
            outcome.worker_output,
            node_execution_id=handoff_node.node_execution_id,
        )
        await self._complete_node(
            workflow,
            handoff_node,
            output=handoff_output,
            transition=NodeTransitionRead(
                selected_transition="verify",
                next_node_keys=["deterministic_verification"],
                condition_summary="Handoff was persisted and linked to its Agent Run",
            ),
            evidence=NodeEvidenceRead(handoff_ids=[handoff_output.agent_handoff_id]),
            usage=NodeUsageRead(),
            status_label="Handoff submitted",
            summary="Agent collaboration evidence is available to downstream nodes",
        )
        await self._complete_task_and_agent(task, agent_run)
        return WorkerExecutionRecord(
            task_key=prepared.task_key,
            task_id=task.task_id,
            agent_run_id=agent_run.agent_run_id,
            worker_node_execution_id=node.node_execution_id,
            handoff_node_execution_id=handoff_node.node_execution_id,
            model_attempt_id=outcome.response.id,
            worker_output=outcome.worker_output,
            handoff=handoff_output,
        )

    def _build_worker_execution_groups(
        self,
        topological_task_order: list[str],
        dependency_keys_by_task_key: dict[str, list[str]],
        max_parallel_agents: int,
    ) -> list[list[str]]:
        """Group dependency-ready Task keys without exceeding the requested concurrency."""

        remaining_task_keys = list(topological_task_order)
        completed_task_keys: set[str] = set()
        execution_groups: list[list[str]] = []
        while remaining_task_keys:
            ready_task_keys = [
                task_key
                for task_key in remaining_task_keys
                if set(dependency_keys_by_task_key[task_key]).issubset(completed_task_keys)
            ]
            if not ready_task_keys:
                raise RuntimeError("Validated Agent Task dependencies cannot be scheduled")
            execution_group = ready_task_keys[:max_parallel_agents]
            execution_groups.append(execution_group)
            completed_task_keys.update(execution_group)
            remaining_task_keys = [
                task_key for task_key in remaining_task_keys if task_key not in completed_task_keys
            ]
        return execution_groups

    async def _dispatch_plan(
        self,
        workflow: LlmAgentWorkflowExecution,
        payload: AgentWorkflowCreate,
        plan: ControllerPlanOutput,
        validation: PlanValidationOutput,
        *,
        parent_node_id: str,
    ) -> tuple[
        LlmAgentWorkflowNodeExecution,
        dict[str, LlmTask],
        dict[str, LlmAgentRun],
    ]:
        """Atomically create Controller-planned Tasks, dependencies, and Agent Runs."""

        node = await self._start_node(
            workflow,
            node_key="agent_dispatch",
            node_type="dispatch",
            output_type="agent_dispatch",
            input_values=plan.model_dump(mode="json"),
            source_node_ids=[parent_node_id],
            status_label="Dispatching Agents",
            summary="Creating durable Tasks and role-owned Agent Runs",
        )
        task_by_key: dict[str, LlmTask] = {}
        agent_run_by_task_key: dict[str, LlmAgentRun] = {}
        dependency_task_keys = {item.task_key for item in plan.dependencies}
        for planned_task in plan.tasks:
            task = LlmTask(
                task_id=new_id(),
                run_id=workflow.run_id,
                task_type=planned_task.task_type,
                title=planned_task.title,
                objective=planned_task.objective,
                assigned_role=planned_task.assigned_role,
                status=(
                    TaskStatus.WAITING_FOR_DEPENDENCY
                    if planned_task.task_key in dependency_task_keys
                    else TaskStatus.READY
                ),
                priority=planned_task.priority,
                max_attempts=1,
                current_attempt=0,
                timeout_seconds=planned_task.timeout_seconds,
            )
            self.db_session.add(task)
            task_by_key[planned_task.task_key] = task
        # AgentRun has no ORM relationship to Task, so force all Task parents into the
        # database before adding their foreign-key children.
        await self.db_session.flush()
        for planned_task in plan.tasks:
            binding = payload.role_bindings[planned_task.assigned_role]
            task = task_by_key[planned_task.task_key]
            agent_run = LlmAgentRun(
                agent_run_id=new_id(),
                workflow_execution_id=workflow.workflow_execution_id,
                run_id=workflow.run_id,
                task_id=task.task_id,
                agent_role=planned_task.assigned_role,
                status=AgentRunStatus.PENDING,
                provider=binding.provider.value,
                model=binding.model,
            )
            self.db_session.add(agent_run)
            agent_run_by_task_key[planned_task.task_key] = agent_run
        await self.db_session.flush()
        for dependency in plan.dependencies:
            self.db_session.add(
                LlmTaskDependency(
                    task_id=task_by_key[dependency.task_key].task_id,
                    depends_on_task_id=task_by_key[dependency.depends_on_task_key].task_id,
                    dependency_type=dependency.dependency_type,
                )
            )
        await self.db_session.commit()
        dependency_keys_by_task_key = {
            task_key: [] for task_key in validation.topological_task_order
        }
        for dependency in plan.dependencies:
            dependency_keys_by_task_key[dependency.task_key].append(dependency.depends_on_task_key)
        execution_groups = self._build_worker_execution_groups(
            validation.topological_task_order,
            dependency_keys_by_task_key,
            payload.max_parallel_agents,
        )
        output = AgentDispatchOutput(
            created_tasks=[
                {
                    "task_key": task_key,
                    "task_id": task.task_id,
                    "status": task.status.value,
                }
                for task_key, task in task_by_key.items()
            ],
            created_agent_runs=[
                {
                    "task_id": task_by_key[task_key].task_id,
                    "agent_run_id": agent_run.agent_run_id,
                    "agent_role": agent_run.agent_role,
                    "provider": agent_run.provider or "",
                    "model": agent_run.model or "",
                    "status": agent_run.status.value,
                }
                for task_key, agent_run in agent_run_by_task_key.items()
            ],
            dispatch_groups=[
                {
                    "group_id": f"worker-group-{group_index}",
                    "agent_run_ids": [agent_run_by_task_key[key].agent_run_id for key in task_keys],
                    "concurrency_limit": min(payload.max_parallel_agents, len(task_keys)),
                }
                for group_index, task_keys in enumerate(execution_groups, start=1)
            ],
            blocked_tasks=[],
            skipped_tasks=[],
            role_model_bindings=[
                {
                    "agent_role": role,
                    "provider": binding.provider.value,
                    "model": binding.model,
                }
                for role, binding in payload.role_bindings.items()
            ],
        )
        await self._complete_node(
            workflow,
            node,
            output=output,
            transition=NodeTransitionRead(
                selected_transition="execute_workers",
                next_node_keys=[
                    f"worker_execution.{key}" for key in validation.topological_task_order
                ],
                condition_summary="Tasks and Agent Runs were created atomically",
            ),
            evidence=NodeEvidenceRead(),
            usage=NodeUsageRead(),
            status_label="Agents dispatched",
            summary="Durable Tasks and Agent Runs are ready for dependency-aware execution",
        )
        return node, task_by_key, agent_run_by_task_key

    async def _call_typed_model(
        self,
        workflow: LlmAgentWorkflowExecution,
        node: LlmAgentWorkflowNodeExecution,
        *,
        binding: AgentModelBinding,
        role: str,
        purpose: str,
        model_output: type[BaseModel],
        model_input: dict,
        timeout_seconds: int | None = None,
    ) -> tuple[Any, ResponsesResult]:
        """Delegate one typed Provider call to the model-node execution service."""

        return await self.model_node_service.call_typed_model(
            workflow,
            node,
            binding=binding,
            role=role,
            purpose=purpose,
            model_output=model_output,
            model_input=model_input,
            timeout_seconds=timeout_seconds,
        )

    async def _start_node(
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
        """Delegate running-node persistence to the workflow state service."""

        return await self.workflow_state.start_node(
            workflow,
            node_key=node_key,
            node_type=node_type,
            output_type=output_type,
            input_values=input_values,
            status_label=status_label,
            summary=summary,
            source_node_ids=source_node_ids,
            handoff_ids=handoff_ids,
            task_id=task_id,
            agent_run_id=agent_run_id,
            agent_turn_id=agent_turn_id,
            agent_role=agent_role,
        )

    async def _complete_node(
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
    ) -> None:
        """Delegate completed-node validation and persistence to the state service."""

        await self.workflow_state.complete_node(
            workflow,
            node,
            output=output,
            transition=transition,
            evidence=evidence,
            usage=usage,
            status_label=status_label,
            summary=summary,
        )

    async def _fail_node(
        self,
        workflow: LlmAgentWorkflowExecution,
        node: LlmAgentWorkflowNodeExecution,
        error: WorkflowErrorRead,
        *,
        cancelled: bool = False,
    ) -> None:
        """Delegate failed, cancelled, or unknown node persistence to the state service."""

        await self.workflow_state.fail_node(
            workflow,
            node=node,
            error=error,
            cancelled=cancelled,
        )

    async def _submit_worker_handoff(
        self,
        workflow: LlmAgentWorkflowExecution,
        task: LlmTask,
        agent_run: LlmAgentRun,
        worker_output: AgentWorkerModelOutput,
        *,
        node_execution_id: str,
    ) -> AgentHandoffOutput:
        """Persist one model-only Handoff using the existing collaboration contract."""

        handoff_json = {
            "objective": task.objective,
            "status": "completed",
            "confirmed_facts": worker_output.confirmed_facts,
            "decisions": worker_output.decisions,
            "files_read": [],
            "files_changed": [],
            "artifacts": [],
            "tests": [],
            "remaining_work": worker_output.remaining_work,
            "risks": worker_output.risks,
            "unknowns": worker_output.unknowns,
            "invariants_for_next_agent": [],
        }
        result = await submit_handoff(
            self.db_session,
            self.storage,
            workflow.run_id,
            AgentHandoffCreate(
                agent_run_id=agent_run.agent_run_id,
                task_id=task.task_id,
                schema_version="agent_handoff.v1",
                status=HandoffStatus.COMPLETED,
                handoff_json=handoff_json,
                idempotency_key=f"workflow:{node_execution_id}",
            ),
        )
        return AgentHandoffOutput(
            agent_handoff_id=result.record.agent_handoff_id,
            schema_version="agent_handoff.v1",
            status="completed",
            supersedes_handoff_id=None,
            **{key: value for key, value in handoff_json.items() if key != "status"},
        )

    async def _verify_handoffs(
        self,
        workflow: LlmAgentWorkflowExecution,
        worker_records: list[WorkerExecutionRecord],
        *,
        source_node_ids: list[str],
    ) -> tuple[DeterministicVerificationOutput, list[str], str]:
        """Verify every persisted worker fact and preserve its explicit Task ownership."""

        node = await self._start_node(
            workflow,
            node_key="deterministic_verification",
            node_type="verification",
            output_type="deterministic_verification",
            input_values={
                "handoff_ids": [record.handoff.agent_handoff_id for record in worker_records]
            },
            source_node_ids=source_node_ids,
            handoff_ids=[record.handoff.agent_handoff_id for record in worker_records],
            status_label="Verifying Agent results",
            summary="Running deterministic Handoff and Attempt checks",
        )
        checks: list[VerificationCheck] = []
        evaluation_ids: list[str] = []
        for index, record in enumerate(worker_records):
            handoff_record = await self.db_session.get(
                LlmAgentHandoff,
                record.handoff.agent_handoff_id,
            )
            model_attempt = await self.db_session.get(
                LlmModelAttempt,
                record.model_attempt_id,
            )
            agent_run = await self.db_session.get(LlmAgentRun, record.agent_run_id)
            passed = all(
                [
                    handoff_record is not None,
                    model_attempt is not None,
                    agent_run is not None,
                    bool(record.handoff.objective.strip()),
                    handoff_record is not None
                    and handoff_record.run_id == workflow.run_id
                    and handoff_record.task_id == record.task_id
                    and handoff_record.agent_run_id == record.agent_run_id
                    and handoff_record.status == HandoffStatus.COMPLETED,
                    model_attempt is not None
                    and model_attempt.run_id == workflow.run_id
                    and model_attempt.task_id == record.task_id
                    and model_attempt.status == AttemptStatus.COMPLETED,
                    agent_run is not None
                    and agent_run.workflow_execution_id == workflow.workflow_execution_id
                    and agent_run.task_id == record.task_id
                    and agent_run.status == AgentRunStatus.COMPLETED,
                ]
            )
            checks.append(
                VerificationCheck(
                    check_id=f"worker-evidence-{index + 1}",
                    check_type="worker_evidence_ownership",
                    status="pass" if passed else "fail",
                    expected=(
                        "Completed Task-owned Agent Run, Model Attempt, and agent_handoff.v1"
                    ),
                    actual=(
                        "Worker evidence ownership is consistent"
                        if passed
                        else "Worker evidence is missing, incomplete, or assigned incorrectly"
                    ),
                    evidence_refs=[
                        record.handoff.agent_handoff_id,
                        record.model_attempt_id,
                    ],
                    error_code=None if passed else "agent_handoff_invalid",
                )
            )
            evaluation = LlmTaskEvaluation(
                evaluation_id=new_id(),
                run_id=workflow.run_id,
                task_id=record.task_id,
                candidate_attempt_id=record.model_attempt_id,
                evaluation_type="agent_deterministic_verification",
                idempotency_key=f"workflow:{workflow.workflow_execution_id}:verify:{index}",
                status=EvaluationStatus.COMPLETED,
                verdict=(EvaluationVerdict.PASS if passed else EvaluationVerdict.FAIL),
                findings_json={
                    "schema_version": "agent_verification.v1",
                    "task_key": record.task_key,
                    "handoff_id": record.handoff.agent_handoff_id,
                    "passed": passed,
                },
                completed_at=utc_now(),
            )
            self.db_session.add(evaluation)
            evaluation_ids.append(evaluation.evaluation_id)
        await self.db_session.commit()
        failed_checks = [check for check in checks if check.status == "fail"]
        requires_independent_review = workflow.review_policy == "always" or (
            workflow.review_policy == "on_verification_failure" and bool(failed_checks)
        )
        output = DeterministicVerificationOutput(
            verdict="fail" if failed_checks else "pass",
            checks=checks,
            blocking_findings=[check.actual for check in failed_checks],
            non_blocking_findings=[],
            verified_claims=["Each Agent output has a persisted Handoff and Model Attempt"],
            unverified_claims=[
                "No file, command, test, network, or external evidence was verified"
            ],
            coverage_summary="Validated model-only Handoff structure and durable references",
            requires_independent_review=requires_independent_review,
        )
        await self._complete_node(
            workflow,
            node,
            output=output,
            transition=NodeTransitionRead(
                selected_transition=(
                    "review" if output.requires_independent_review else "synthesize"
                ),
                next_node_keys=[
                    "independent_review"
                    if output.requires_independent_review
                    else "final_synthesis"
                ],
                condition_summary="Deterministic verification completed",
            ),
            evidence=NodeEvidenceRead(
                model_attempt_ids=[record.model_attempt_id for record in worker_records],
                evaluation_ids=evaluation_ids,
                handoff_ids=[record.handoff.agent_handoff_id for record in worker_records],
            ),
            usage=NodeUsageRead(),
            status_label="Verification complete",
            summary="Deterministic checks recorded verified and unverified claims",
        )
        return output, evaluation_ids, node.node_execution_id

    async def _run_reviewer(
        self,
        workflow: LlmAgentWorkflowExecution,
        payload: AgentWorkflowCreate,
        run: LlmRun,
        worker_records: list[WorkerExecutionRecord],
        verification: DeterministicVerificationOutput,
        evaluation_ids: list[str],
        *,
        verification_node_execution_id: str,
    ) -> tuple[IndependentReviewOutput, str]:
        """Run a separate Reviewer Agent over explicit candidate and verification evidence."""

        binding = payload.role_bindings.get("reviewer")
        if binding is None:
            raise InvalidRequestError("Reviewer role binding is required by review policy")
        task = LlmTask(
            task_id=new_id(),
            run_id=workflow.run_id,
            task_type="independent_review",
            title="Independently review Agent results",
            objective="Review the candidate handoffs against deterministic verification.",
            assigned_role="reviewer",
            status=TaskStatus.RUNNING,
            max_attempts=1,
            current_attempt=1,
            timeout_seconds=binding.timeout_seconds,
            started_at=utc_now(),
        )
        agent_run = LlmAgentRun(
            agent_run_id=new_id(),
            workflow_execution_id=workflow.workflow_execution_id,
            run_id=workflow.run_id,
            task_id=task.task_id,
            agent_role="reviewer",
            status=AgentRunStatus.RUNNING,
            provider=binding.provider.value,
            model=binding.model,
            started_at=utc_now(),
        )
        self.db_session.add(task)
        await self.db_session.flush()
        self.db_session.add(agent_run)
        await self.db_session.commit()
        node = await self._start_node(
            workflow,
            node_key="independent_review",
            node_type="agent_model_execution",
            output_type="independent_review",
            input_values={
                "objective": run.user_request,
                "worker_results": [self._worker_record_input(record) for record in worker_records],
                "verification": verification.model_dump(mode="json"),
            },
            source_node_ids=[
                *[record.handoff_node_execution_id for record in worker_records],
                verification_node_execution_id,
            ],
            handoff_ids=[record.handoff.agent_handoff_id for record in worker_records],
            task_id=task.task_id,
            agent_run_id=agent_run.agent_run_id,
            agent_role="reviewer",
            status_label="Independent review running",
            summary="Reviewer is checking candidate claims against explicit evidence",
        )
        turn = await self._start_agent_turn(agent_run, node)
        reviewer_output, response = await self._call_typed_model(
            workflow,
            node,
            binding=binding,
            role="reviewer",
            purpose="Independently review candidate handoffs and deterministic verification.",
            model_output=ReviewerModelOutput,
            model_input={
                "objective": run.user_request,
                "worker_results": [self._worker_record_input(record) for record in worker_records],
                "verification": verification.model_dump(mode="json"),
            },
            timeout_seconds=task.timeout_seconds,
        )
        await self._complete_agent_turn(turn, agent_run, response.id)
        node.agent_turn_id = turn.agent_turn_id
        reviewer_evaluation_ids: list[str] = []
        for index, record in enumerate(worker_records):
            evaluation = LlmTaskEvaluation(
                evaluation_id=new_id(),
                run_id=workflow.run_id,
                task_id=record.task_id,
                candidate_attempt_id=record.model_attempt_id,
                evaluator_attempt_id=response.id,
                evaluation_type="agent_independent_review",
                idempotency_key=(f"workflow:{workflow.workflow_execution_id}:review:{index}"),
                status=EvaluationStatus.COMPLETED,
                verdict=(
                    EvaluationVerdict.PASS
                    if reviewer_output.verdict == "pass"
                    else EvaluationVerdict.FAIL
                ),
                score=reviewer_output.score * Decimal("100"),
                findings_json={
                    "schema_version": "agent_independent_review.v1",
                    "task_key": record.task_key,
                    "handoff_id": record.handoff.agent_handoff_id,
                    "all_candidate_attempt_ids": [item.model_attempt_id for item in worker_records],
                    **reviewer_output.model_dump(mode="json"),
                },
                completed_at=utc_now(),
            )
            self.db_session.add(evaluation)
            reviewer_evaluation_ids.append(evaluation.evaluation_id)
        await self.db_session.commit()
        output = IndependentReviewOutput(
            **reviewer_output.model_dump(),
            evaluation_ids=reviewer_evaluation_ids,
            reviewer_task_id=task.task_id,
            reviewer_agent_run_id=agent_run.agent_run_id,
            reviewer_model_attempt_id=response.id,
        )
        review_accepted = (
            reviewer_output.verdict == "pass"
            and not reviewer_output.requires_replan
            and not reviewer_output.requires_retry
        )
        await self._complete_node(
            workflow,
            node,
            output=output,
            transition=NodeTransitionRead(
                selected_transition=("synthesize" if review_accepted else "reject"),
                next_node_keys=(["final_synthesis"] if review_accepted else []),
                condition_summary="Independent Reviewer returned a typed verdict",
            ),
            evidence=self._model_evidence(
                response,
                evaluation_ids=evaluation_ids + reviewer_evaluation_ids,
                handoff_ids=[record.handoff.agent_handoff_id for record in worker_records],
            ),
            usage=self._model_usage(response),
            status_label="Independent review complete",
            summary="Reviewer recorded a verdict without modifying candidate evidence",
        )
        await self._complete_task_and_agent(task, agent_run)
        return output, node.node_execution_id

    async def _complete_workflow(
        self,
        workflow: LlmAgentWorkflowExecution,
        run: LlmRun,
        *,
        final_node_id: str,
        task_ids: list[str],
        agent_run_ids: list[str],
        handoff_ids: list[str],
        evaluation_ids: list[str],
    ) -> None:
        """Persist the aggregation node, then atomically mark workflow and Run completed."""

        existing_nodes = list(
            (
                await self.db_session.scalars(
                    select(LlmAgentWorkflowNodeExecution)
                    .where(
                        LlmAgentWorkflowNodeExecution.workflow_execution_id
                        == workflow.workflow_execution_id
                    )
                    .order_by(LlmAgentWorkflowNodeExecution.node_sequence)
                )
            ).all()
        )
        model_attempt_ids = [
            attempt_id
            for node in existing_nodes
            for attempt_id in (node.evidence_json or {}).get("model_attempt_ids", [])
        ]
        total_usage = self._sum_usage(existing_nodes)
        total_duration_ms = max(
            0,
            int(
                (_as_utc(utc_now()) - _as_utc(workflow.started_at or utc_now())).total_seconds()
                * 1_000
            ),
        )
        completion_output = WorkflowCompletionOutput(
            workflow_status="completed",
            final_node_execution_id=final_node_id,
            node_count=len(existing_nodes) + 1,
            node_counts_by_status={
                "completed": len(existing_nodes) + 1,
                "failed": 0,
                "cancelled": 0,
            },
            task_ids=task_ids,
            agent_run_ids=agent_run_ids,
            model_attempt_ids=list(dict.fromkeys(model_attempt_ids)),
            tool_call_ids=[],
            evaluation_ids=evaluation_ids,
            artifact_ids=[],
            handoff_ids=handoff_ids,
            total_input_tokens=total_usage.input_tokens,
            total_output_tokens=total_usage.output_tokens,
            total_cached_tokens=total_usage.cached_tokens,
            total_estimated_cost=total_usage.estimated_cost,
            total_duration_ms=total_duration_ms,
            remaining_model_calls=max(0, workflow.max_model_calls - workflow.model_call_count),
            final_output_reference=final_node_id,
            warnings=[],
            errors=[],
        )
        node = await self._start_node(
            workflow,
            node_key="workflow_completion",
            node_type="aggregation",
            output_type="workflow_completion",
            input_values={"final_node_execution_id": final_node_id},
            source_node_ids=[final_node_id],
            status_label="Workflow complete",
            summary="Agent workflow completed with durable node and evidence records",
        )
        await self.workflow_state.complete_workflow(
            workflow,
            run,
            completion_node=node,
            completion_output=completion_output,
            transition=NodeTransitionRead(
                selected_transition="completed",
                next_node_keys=[],
                condition_summary="All required nodes reached a successful terminal state",
            ),
            evidence=NodeEvidenceRead(),
            usage=NodeUsageRead(),
            status_label="Workflow complete",
            summary="Agent workflow completed with durable node and evidence records",
        )

    async def _fail_workflow(
        self,
        workflow: LlmAgentWorkflowExecution,
        *,
        error: WorkflowErrorRead,
    ) -> None:
        """Delegate failed or unknown workflow state cleanup to the state service."""

        await self.workflow_state.fail_workflow(
            workflow,
            error=error,
        )

    async def _cancel_workflow(self, workflow: LlmAgentWorkflowExecution) -> None:
        """Delegate request cancellation and child-state cleanup to the state service."""

        cancellation_error = self._workflow_error(
            "agent_workflow_cancelled",
            "Workflow request was cancelled.",
        )
        await self.workflow_state.cancel_workflow(
            workflow,
            error=cancellation_error,
        )

    async def _append_event(
        self,
        workflow: LlmAgentWorkflowExecution,
        *,
        event_type: str,
        public_summary: str,
        public_payload: dict,
        node: LlmAgentWorkflowNodeExecution | None = None,
    ) -> LlmAgentWorkflowEvent:
        """Delegate committed event persistence and observer delivery to the state service."""

        return await self.workflow_state.append_event(
            workflow,
            event_type=event_type,
            public_summary=public_summary,
            public_payload=public_payload,
            node=node,
        )

    async def _ensure_workflow_running(
        self,
        workflow: LlmAgentWorkflowExecution,
    ) -> None:
        """Refresh workflow state and stop work after cancellation, failure, or deadline expiry."""

        await self.workflow_state.ensure_workflow_running(workflow)

    def _remaining_wall_time_ms(self, workflow: LlmAgentWorkflowExecution) -> int:
        """Return non-negative wall-clock budget remaining for this synchronous workflow."""

        return self.workflow_state.remaining_wall_time_ms(workflow)

    async def _budget(
        self,
        workflow: LlmAgentWorkflowExecution,
        *,
        node_count: int,
        cost_used_before: Decimal | None = None,
    ) -> NodeBudgetRead:
        """Calculate a node budget projection from current durable Run and workflow values."""

        return await self.workflow_state.build_node_budget(
            workflow,
            node_count=node_count,
            cost_used_before=cost_used_before,
        )

    async def _start_task_and_agent(self, task: LlmTask, agent_run: LlmAgentRun) -> None:
        """Delegate Task and Agent Run start transitions to the state service."""

        await self.workflow_state.start_task_and_agent(task, agent_run)

    async def _complete_task_and_agent(self, task: LlmTask, agent_run: LlmAgentRun) -> None:
        """Delegate Task and Agent Run completion to the state service."""

        await self.workflow_state.complete_task_and_agent(task, agent_run)

    async def _start_agent_turn(
        self,
        agent_run: LlmAgentRun,
        node: LlmAgentWorkflowNodeExecution,
    ) -> LlmAgentTurn:
        """Delegate Agent Turn creation to the state service."""

        return await self.workflow_state.start_agent_turn(agent_run, node)

    async def _complete_agent_turn(
        self,
        turn: LlmAgentTurn,
        agent_run: LlmAgentRun,
        model_attempt_id: str,
    ) -> None:
        """Delegate Agent Turn completion and Attempt binding to the state service."""

        await self.workflow_state.complete_agent_turn(turn, agent_run, model_attempt_id)

    async def _save_final_message(
        self,
        run: LlmRun,
        output: FinalSynthesisModelOutput,
    ) -> str | None:
        """Delegate final Assistant Message staging to the state service."""

        return await self.workflow_state.save_final_message(run, output)

    async def _require_run(self, run_id: str) -> LlmRun:
        """Return the owning Run or raise a stable resource-not-found failure."""

        return await self.workflow_state.require_run(run_id)

    async def _node_id_by_key(self, workflow_execution_id: str, node_key: str) -> str:
        """Delegate committed node identity lookup to the state service."""

        return await self.workflow_state.node_id_by_key(workflow_execution_id, node_key)

    async def _transport_attempt_count(self, model_attempt_id: str) -> int:
        """Delegate physical Provider-attempt counting to the state service."""

        return await self.workflow_state.transport_attempt_count(model_attempt_id)

    def _worker_record_input(self, record: WorkerExecutionRecord) -> dict[str, Any]:
        """Return one bounded worker result for dependent Agents and final synthesis."""

        return {
            "task_key": record.task_key,
            "task_id": record.task_id,
            "agent_run_id": record.agent_run_id,
            "model_attempt_id": record.model_attempt_id,
            "worker_output": record.worker_output.model_dump(mode="json"),
            "handoff": record.handoff.model_dump(mode="json"),
        }

    async def _node_attempt_evidence_and_usage(
        self,
        workflow: LlmAgentWorkflowExecution,
        node: LlmAgentWorkflowNodeExecution,
    ) -> tuple[NodeEvidenceRead, NodeUsageRead]:
        """Delegate model Attempt recovery to the workflow state service."""

        return await self.workflow_state.node_attempt_evidence_and_usage(workflow, node)

    async def _node_has_outcome_unknown_attempt(
        self,
        workflow: LlmAgentWorkflowExecution,
        node: LlmAgentWorkflowNodeExecution,
    ) -> bool:
        """Delegate unknown Provider outcome lookup to the workflow state service."""

        return await self.workflow_state.node_has_outcome_unknown_attempt(workflow, node)

    def _model_usage(self, response: ResponsesResult) -> NodeUsageRead:
        """Map one public model result to a node usage aggregate."""

        return NodeUsageRead(
            input_tokens=response.usage.input_tokens or 0,
            output_tokens=response.usage.output_tokens or 0,
            cached_tokens=response.usage.cached_tokens or 0,
            estimated_cost=Decimal(response.usage.estimated_cost or "0"),
            model_call_count=1,
            tool_call_count=0,
        )

    def _model_evidence(
        self,
        response: ResponsesResult,
        *,
        evaluation_ids: list[str] | None = None,
        handoff_ids: list[str] | None = None,
    ) -> NodeEvidenceRead:
        """Reference the existing model Attempt and optional downstream evidence facts."""

        return NodeEvidenceRead(
            model_attempt_ids=[response.id],
            evaluation_ids=evaluation_ids or [],
            handoff_ids=handoff_ids or [],
        )

    def _sum_usage(
        self,
        nodes: list[LlmAgentWorkflowNodeExecution],
    ) -> NodeUsageRead:
        """Sum normalized usage JSON from already committed node results."""

        usages = [NodeUsageRead.model_validate(node.usage_json or {}) for node in nodes]
        return NodeUsageRead(
            input_tokens=sum(item.input_tokens for item in usages),
            output_tokens=sum(item.output_tokens for item in usages),
            cached_tokens=sum(item.cached_tokens for item in usages),
            estimated_cost=sum((item.estimated_cost for item in usages), Decimal("0")),
            model_call_count=sum(item.model_call_count for item in usages),
            tool_call_count=sum(item.tool_call_count for item in usages),
        )

    def _public_error_from_exception(self, error: Exception) -> WorkflowErrorRead:
        """Map expected validation/provider failures to a stable secret-free workflow error."""

        if isinstance(error, ModelProviderError):
            outcome_is_known = error.error_type not in {
                "timeout",
                "network_error",
                "connection_error",
                "internal_error",
                "response_persistence_error",
            }
            return WorkflowErrorRead(
                error_code=error.error_type,
                error_type="provider_error",
                public_message=error.message,
                is_retryable=error.retryable and outcome_is_known,
                outcome_is_known=outcome_is_known,
            )
        if isinstance(error, InvalidRequestError):
            message = str(error)
            lowered_message = message.lower()
            if "price" in lowered_message:
                code = "agent_budget_price_unavailable"
            elif "tool" in lowered_message:
                code = "agent_capability_unavailable"
            elif "budget" in lowered_message:
                code = "agent_budget_exhausted"
            elif "wall-time" in lowered_message:
                code = "agent_workflow_timeout"
            elif "model-call limit" in lowered_message:
                code = "agent_model_call_limit_exceeded"
            elif "node limit" in lowered_message:
                code = "agent_node_limit_exceeded"
            else:
                code = "node_output_invalid"
            return self._workflow_error(code, message)
        return self._workflow_error(
            "agent_workflow_internal_error",
            "Agent workflow execution failed.",
        )

    def _workflow_error(
        self,
        error_code: str,
        message: str,
        *,
        is_retryable: bool = False,
        outcome_is_known: bool = True,
    ) -> WorkflowErrorRead:
        """Build a bounded workflow error with explicit retry and outcome semantics."""

        return WorkflowErrorRead(
            error_code=error_code,
            error_type="workflow_error",
            public_message=message,
            is_retryable=is_retryable,
            outcome_is_known=outcome_is_known,
            details_reference_id=None,
        )

    def _bump_snapshot(self, workflow: LlmAgentWorkflowExecution) -> None:
        """Advance optimistic workflow and result-snapshot versions together."""

        self.workflow_state.bump_snapshot(workflow)


def _as_utc(value: datetime) -> datetime:
    """Treat SQLite-naive persisted UTC values as UTC for elapsed calculations."""

    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
