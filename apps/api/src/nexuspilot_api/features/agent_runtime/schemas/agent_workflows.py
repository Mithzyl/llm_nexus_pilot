"""Versioned contracts for model-only Agent workflows and complete node results."""

from datetime import datetime
from decimal import Decimal
from typing import Literal

from nexuspilot_models.contracts import ProviderName
from pydantic import BaseModel, ConfigDict, Field, model_validator

from nexuspilot_api.models import AgentWorkflowNodeStatus, AgentWorkflowStatus


class StrictContract(BaseModel):
    """Reject unknown fields in every Agent-generated or public workflow contract."""

    model_config = ConfigDict(extra="forbid")


class AgentModelBinding(StrictContract):
    """Bind one Agent role to an explicit provider/model and structured-output mode."""

    provider: ProviderName
    model: str = Field(min_length=1, max_length=128)
    structured_output_mode: Literal["native_schema", "prompted_json"] = "native_schema"
    timeout_seconds: int = Field(default=60, ge=1, le=600)
    max_output_tokens: int = Field(default=4_096, ge=1, le=32_000)


class AgentWorkflowCreate(StrictContract):
    """Validate one synchronous model-only workflow creation request."""

    workflow_name: Literal["model_only"] = "model_only"
    workflow_version: Literal["1.0.0"] = "1.0.0"
    execution_profile: Literal["model_only_v1"] = "model_only_v1"
    idempotency_key: str = Field(min_length=8, max_length=128)
    role_bindings: dict[
        Literal["controller", "planner", "researcher", "reviewer", "verifier"],
        AgentModelBinding,
    ]
    review_policy: Literal["always", "on_verification_failure", "never"] = "always"
    max_nodes: int = Field(default=32, ge=10, le=32)
    max_model_calls: int = Field(default=16, ge=3, le=16)
    max_parallel_agents: Literal[1] = 1
    wall_time_limit_ms: int = Field(default=600_000, ge=1_000, le=600_000)
    stream: bool = False

    @model_validator(mode="after")
    def validate_required_role_bindings(self) -> "AgentWorkflowCreate":
        """Require roles needed before a model-generated plan can be evaluated."""

        required_roles = {"controller"}
        if not {"planner", "researcher"}.intersection(self.role_bindings):
            raise ValueError("role_bindings requires at least one planner or researcher")
        if self.review_policy != "never":
            required_roles.add("reviewer")
        missing_roles = sorted(required_roles - set(self.role_bindings))
        if missing_roles:
            raise ValueError(f"role_bindings is missing required roles: {missing_roles}")
        minimum_model_call_count = 3 if self.review_policy == "never" else 4
        if self.max_model_calls < minimum_model_call_count:
            raise ValueError(
                "max_model_calls is too small for Controller, Worker, final synthesis, "
                "and the configured review policy"
            )
        minimum_node_count = 10 if self.review_policy == "never" else 11
        if self.max_nodes < minimum_node_count:
            raise ValueError(
                f"max_nodes must be at least {minimum_node_count} for this review policy"
            )
        return self


class WorkflowErrorRead(StrictContract):
    """Expose a stable, secret-free workflow or node failure classification."""

    error_code: str
    error_type: str
    public_message: str
    is_retryable: bool
    outcome_is_known: bool
    details_reference_id: str | None = None


class NodeInputRead(StrictContract):
    """Identify exact persisted inputs without copying prompts or private contents."""

    schema_version: str
    input_hash: str
    source_node_execution_ids: list[str] = Field(default_factory=list)
    message_ids: list[str] = Field(default_factory=list)
    artifact_ids: list[str] = Field(default_factory=list)
    handoff_ids: list[str] = Field(default_factory=list)
    context_build_id: str | None = None
    prompt_release_id: str | None = None


class NodeTransitionRead(StrictContract):
    """Describe the deterministic branch selected after a node result."""

    selected_transition: str | None = None
    next_node_keys: list[str] = Field(default_factory=list)
    skipped_node_keys: list[str] = Field(default_factory=list)
    condition_summary: str = Field(default="", max_length=2_000)


class NodeEvidenceRead(StrictContract):
    """Reference durable evidence owned by Attempt, Evaluation, Artifact, or Handoff tables."""

    model_attempt_ids: list[str] = Field(default_factory=list)
    tool_call_ids: list[str] = Field(default_factory=list)
    evaluation_ids: list[str] = Field(default_factory=list)
    artifact_ids: list[str] = Field(default_factory=list)
    handoff_ids: list[str] = Field(default_factory=list)


class NodeUsageRead(StrictContract):
    """Expose bounded node usage aggregated from its referenced model attempts."""

    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    cached_tokens: int = Field(default=0, ge=0)
    estimated_cost: Decimal = Decimal("0")
    model_call_count: int = Field(default=0, ge=0)
    tool_call_count: int = Field(default=0, ge=0)


class NodeBudgetRead(StrictContract):
    """Expose remaining workflow limits after one node transition."""

    cost_limit: Decimal | None = None
    cost_used_before: Decimal = Decimal("0")
    cost_used_after: Decimal = Decimal("0")
    remaining_cost: Decimal | None = None
    remaining_model_calls: int = Field(ge=0)
    remaining_nodes: int = Field(ge=0)
    remaining_wall_time_ms: int = Field(ge=0)


class NodeTimingRead(StrictContract):
    """Expose persisted node start, completion, and monotonic elapsed duration."""

    started_at: datetime
    completed_at: datetime | None
    duration_ms: int = Field(ge=0)


class NodePublicViewRead(StrictContract):
    """Provide deterministic user-facing node status without model-authored progress claims."""

    status_label: str = Field(min_length=1, max_length=128)
    summary: str = Field(min_length=1, max_length=2_000)
    progress_current: int = Field(ge=0)
    progress_total: int = Field(ge=1)


class NodeObservabilityRead(StrictContract):
    """Carry optional trace correlation identifiers without making telemetry authoritative."""

    trace_id: str | None = None
    span_id: str | None = None


class RequestIntakeOutput(StrictContract):
    """Return the normalized workflow objective and explicit capability boundary."""

    normalized_objective: str = Field(min_length=1, max_length=100_000)
    request_type: str = Field(min_length=1, max_length=64)
    complexity: Literal["simple", "complex"]
    requires_decomposition: bool
    constraints: list[str] = Field(default_factory=list, max_length=50)
    acceptance_criteria: list[str] = Field(default_factory=list, max_length=50)
    explicit_assumptions: list[str] = Field(default_factory=list, max_length=20)
    clarification_questions: list[str] = Field(default_factory=list, max_length=20)
    required_capabilities: list[str] = Field(default_factory=list, max_length=20)
    unavailable_capabilities: list[str] = Field(default_factory=list, max_length=20)
    requested_output_format: str = "text"
    language: str = "auto"


class ExcludedContextSource(StrictContract):
    """Describe one context source omitted by deterministic assembly policy."""

    source_type: str = Field(min_length=1, max_length=64)
    source_id: str | None = Field(default=None, max_length=128)
    reason: str = Field(min_length=1, max_length=512)


class ContextAssemblyOutput(StrictContract):
    """Return the exact bounded sources assembled for Controller planning."""

    included_run_id: str
    included_message_ids: list[str] = Field(default_factory=list)
    included_artifact_ids: list[str] = Field(default_factory=list)
    included_handoff_ids: list[str] = Field(default_factory=list)
    excluded_sources: list[ExcludedContextSource] = Field(default_factory=list, max_length=100)
    input_character_count: int = Field(ge=0)
    is_truncated: bool
    memory_packet_id: None = None


class PlannedAgentTask(StrictContract):
    """Describe one complete task proposed by Controller for a registered Agent role."""

    task_key: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,63}$")
    task_type: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=255)
    objective: str = Field(min_length=1, max_length=10_000)
    assigned_role: str = Field(min_length=1, max_length=64)
    required_capabilities: list[str] = Field(default_factory=list, max_length=20)
    expected_output_type: Literal["agent_handoff"]
    completion_criteria: list[str] = Field(min_length=1, max_length=20)
    priority: int = Field(default=0, ge=-100, le=100)
    timeout_seconds: int = Field(default=300, ge=1, le=600)
    max_model_calls: Literal[1] = 1


class PlannedTaskDependency(StrictContract):
    """Describe one completion dependency between Controller-planned task keys."""

    task_key: str
    depends_on_task_key: str
    dependency_type: Literal["completion"] = "completion"


class ControllerPlanOutput(StrictContract):
    """Return the only model-authored plan shape accepted by model_only_v1."""

    decision_summary: str = Field(min_length=1, max_length=2_000)
    tasks: list[PlannedAgentTask] = Field(min_length=1, max_length=8)
    dependencies: list[PlannedTaskDependency] = Field(default_factory=list, max_length=32)
    review_policy: Literal["always", "on_verification_failure", "never"]
    known_risks: list[str] = Field(default_factory=list, max_length=20)
    unknowns: list[str] = Field(default_factory=list, max_length=20)


class PlanValidationCheck(StrictContract):
    """Expose one deterministic Controller-plan validation outcome."""

    check_id: str
    status: Literal["pass", "fail"]
    error_code: str | None = None
    message: str
    related_task_keys: list[str] = Field(default_factory=list)


class PlanValidationOutput(StrictContract):
    """Return normalized order and every deterministic Controller-plan check."""

    is_valid: bool
    checks: list[PlanValidationCheck]
    topological_task_order: list[str]
    cycle_paths: list[list[str]] = Field(default_factory=list)
    total_task_count: int = Field(ge=0)
    total_model_call_limit: int = Field(ge=0)
    capability_gaps: list[dict[str, str]] = Field(default_factory=list)
    relationship_errors: list[str] = Field(default_factory=list)
    rejected_plan_reason: str | None = None


class DispatchedAgentTask(StrictContract):
    """Identify one durable Task created from a validated Controller plan item."""

    task_key: str
    task_id: str
    status: Literal["ready", "waiting_for_dependency"]


class DispatchedAgentRun(StrictContract):
    """Identify one role-owned Agent Run and its explicit provider binding."""

    task_id: str
    agent_run_id: str
    agent_role: str
    provider: ProviderName
    model: str = Field(min_length=1, max_length=128)
    status: Literal["pending"]


class AgentDispatchGroup(StrictContract):
    """Describe one deterministic execution group and its concurrency limit."""

    group_id: str = Field(min_length=1, max_length=128)
    agent_run_ids: list[str] = Field(min_length=1, max_length=8)
    concurrency_limit: Literal[1] = 1


class BlockedAgentTask(StrictContract):
    """Describe a validated Task that could not be dispatched and why."""

    task_key: str
    task_id: str | None = None
    reason_code: str
    summary: str


class SkippedAgentTask(StrictContract):
    """Describe a planned Task intentionally omitted by a deterministic transition."""

    task_key: str
    reason_code: str


class AgentRoleModelBindingRead(StrictContract):
    """Expose one role's exact provider and model selected for this workflow."""

    agent_role: str
    provider: ProviderName
    model: str = Field(min_length=1, max_length=128)


class AgentDispatchOutput(StrictContract):
    """Return the persisted Task and Agent Run identities created by dispatch."""

    created_tasks: list[DispatchedAgentTask]
    created_agent_runs: list[DispatchedAgentRun]
    dispatch_groups: list[AgentDispatchGroup]
    blocked_tasks: list[BlockedAgentTask] = Field(default_factory=list)
    skipped_tasks: list[SkippedAgentTask] = Field(default_factory=list)
    role_model_bindings: list[AgentRoleModelBindingRead]


class AgentWorkerModelOutput(StrictContract):
    """Validate the bounded evidence summary produced by a model-only work Agent."""

    summary: str = Field(min_length=1, max_length=12_000)
    confirmed_facts: list[str] = Field(default_factory=list, max_length=50)
    decisions: list[str] = Field(default_factory=list, max_length=50)
    remaining_work: list[str] = Field(default_factory=list, max_length=50)
    risks: list[str] = Field(default_factory=list, max_length=50)
    unknowns: list[str] = Field(default_factory=list, max_length=50)


class AgentModelExecutionOutput(StrictContract):
    """Return one Agent Turn and normalized model invocation result."""

    agent_turn_id: str
    model_attempt_id: str
    provider: ProviderName
    model: str
    response_kind: Literal["structured"] = "structured"
    text_preview: str | None = Field(default=None, max_length=1_000)
    text_artifact_id: str | None = None
    structured_output: AgentWorkerModelOutput
    requested_tool_calls: list[dict] = Field(default_factory=list, max_length=0)
    finish_reason: str
    provider_request_id: str | None = None
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    cached_tokens: int | None = Field(default=None, ge=0)
    estimated_cost: Decimal | None = None
    latency_ms: int = Field(ge=0)
    transport_attempt_count: int = Field(ge=0)
    capability_warnings: list[str] = Field(default_factory=list)


class AgentHandoffOutput(StrictContract):
    """Expose the existing immutable agent_handoff.v1 resource and its typed content."""

    agent_handoff_id: str
    schema_version: Literal["agent_handoff.v1"]
    status: Literal["completed", "partial"]
    objective: str
    confirmed_facts: list[str]
    decisions: list[str]
    files_read: list[str]
    files_changed: list[str]
    artifacts: list[str]
    tests: list[str]
    remaining_work: list[str]
    risks: list[str]
    unknowns: list[str]
    invariants_for_next_agent: list[str]
    supersedes_handoff_id: str | None = None


class VerificationCheck(StrictContract):
    """Expose one deterministic verification assertion and evidence references."""

    check_id: str
    check_type: str
    status: Literal["pass", "fail", "unknown"]
    expected: str
    actual: str
    evidence_refs: list[str] = Field(default_factory=list)
    error_code: str | None = None


class DeterministicVerificationOutput(StrictContract):
    """Return programmatic checks that a Reviewer cannot override."""

    verdict: Literal["pass", "fail", "unknown"]
    checks: list[VerificationCheck]
    blocking_findings: list[str] = Field(default_factory=list)
    non_blocking_findings: list[str] = Field(default_factory=list)
    verified_claims: list[str] = Field(default_factory=list)
    unverified_claims: list[str] = Field(default_factory=list)
    coverage_summary: str
    requires_independent_review: bool


class ReviewerFinding(StrictContract):
    """Return one bounded independent-review finding with explicit evidence."""

    finding_id: str
    severity: Literal["error", "warning", "info"]
    category: str
    description: str
    evidence_refs: list[str] = Field(default_factory=list)
    required_action: str | None = None


class ReviewerModelOutput(StrictContract):
    """Validate the model-authored part of one independent review."""

    verdict: Literal["pass", "fail", "needs_revision"]
    score: Decimal = Field(ge=0, le=1)
    findings: list[ReviewerFinding] = Field(default_factory=list, max_length=50)
    accepted_claims: list[str] = Field(default_factory=list, max_length=50)
    rejected_claims: list[str] = Field(default_factory=list, max_length=50)
    missing_evidence: list[str] = Field(default_factory=list, max_length=50)
    requires_replan: bool
    requires_retry: bool
    review_summary: str = Field(min_length=1, max_length=4_000)

    @model_validator(mode="after")
    def validate_passing_verdict_has_no_blocking_finding(self) -> "ReviewerModelOutput":
        """Reject a passing verdict when the same review reports an error finding."""

        if self.verdict == "pass" and any(finding.severity == "error" for finding in self.findings):
            raise ValueError("A passing review cannot contain an error finding")
        return self


class IndependentReviewOutput(ReviewerModelOutput):
    """Return the Reviewer Agent, model Attempt, and persisted Evaluation identities."""

    evaluation_ids: list[str] = Field(min_length=1)
    reviewer_task_id: str
    reviewer_agent_run_id: str
    reviewer_model_attempt_id: str


class FinalSynthesisModelOutput(StrictContract):
    """Validate the final Controller answer before it becomes a conversation message."""

    answer_type: Literal["text"] = "text"
    final_text: str = Field(min_length=1, max_length=16_000)
    completed_objectives: list[str] = Field(default_factory=list, max_length=50)
    unresolved_items: list[str] = Field(default_factory=list, max_length=50)
    warnings: list[str] = Field(default_factory=list, max_length=50)
    recommended_next_actions: list[str] = Field(default_factory=list, max_length=50)


class FinalSynthesisOutput(FinalSynthesisModelOutput):
    """Return final evidence references and an optional persisted Assistant Message."""

    source_agent_run_ids: list[str]
    source_handoff_ids: list[str]
    source_artifact_ids: list[str]
    source_evaluation_ids: list[str]
    assistant_message_id: str | None = None


class WorkflowCompletionOutput(StrictContract):
    """Return workflow totals aggregated from durable child facts."""

    workflow_status: Literal["completed"]
    final_node_execution_id: str
    node_count: int = Field(ge=1)
    node_counts_by_status: dict[str, int]
    task_ids: list[str]
    agent_run_ids: list[str]
    model_attempt_ids: list[str]
    tool_call_ids: list[str]
    evaluation_ids: list[str]
    artifact_ids: list[str]
    handoff_ids: list[str]
    total_input_tokens: int = Field(ge=0)
    total_output_tokens: int = Field(ge=0)
    total_cached_tokens: int = Field(ge=0)
    total_estimated_cost: Decimal
    total_duration_ms: int = Field(ge=0)
    remaining_model_calls: int = Field(ge=0)
    final_output_reference: str
    warnings: list[str]
    errors: list[WorkflowErrorRead]


NODE_OUTPUT_MODELS: dict[str, type[BaseModel]] = {
    "request_intake": RequestIntakeOutput,
    "context_assembly": ContextAssemblyOutput,
    "controller_plan": ControllerPlanOutput,
    "plan_validation": PlanValidationOutput,
    "agent_dispatch": AgentDispatchOutput,
    "agent_model_execution": AgentModelExecutionOutput,
    "agent_handoff": AgentHandoffOutput,
    "deterministic_verification": DeterministicVerificationOutput,
    "independent_review": IndependentReviewOutput,
    "final_synthesis": FinalSynthesisOutput,
    "workflow_completion": WorkflowCompletionOutput,
}

AgentWorkflowNodeOutput = (
    RequestIntakeOutput
    | ContextAssemblyOutput
    | ControllerPlanOutput
    | PlanValidationOutput
    | AgentDispatchOutput
    | AgentModelExecutionOutput
    | AgentHandoffOutput
    | DeterministicVerificationOutput
    | IndependentReviewOutput
    | FinalSynthesisOutput
    | WorkflowCompletionOutput
)


def validate_node_output(output_type: str, output: dict) -> dict:
    """Validate and normalize one node-specific output before persistence."""

    output_model = NODE_OUTPUT_MODELS.get(output_type)
    if output_model is None:
        raise ValueError(f"Unsupported Agent node output type: {output_type}")
    return output_model.model_validate(output).model_dump(mode="json")


def node_output_schema_version(output_type: str) -> str:
    """Return the only public output-schema version registered for one node type."""

    if output_type not in NODE_OUTPUT_MODELS:
        raise ValueError(f"Unsupported Agent node output type: {output_type}")
    return f"{output_type}_output.v1"


class AgentWorkflowNodeResultRead(StrictContract):
    """Expose one complete, versioned, bounded workflow node result envelope."""

    schema_version: Literal["agent_workflow_node_result.v1"] = "agent_workflow_node_result.v1"
    workflow_execution_id: str
    workflow_name: str
    workflow_version: str
    node_execution_id: str
    node_key: str
    node_type: str
    node_version: str
    node_sequence: int
    node_attempt: int
    parent_node_execution_id: str | None
    run_id: str
    task_id: str | None
    agent_run_id: str | None
    agent_turn_id: str | None
    agent_role: str | None
    status: AgentWorkflowNodeStatus
    input: NodeInputRead
    output_type: str
    output_schema_version: str
    output: AgentWorkflowNodeOutput | None
    transition: NodeTransitionRead
    evidence: NodeEvidenceRead
    usage: NodeUsageRead
    budget: NodeBudgetRead
    timing: NodeTimingRead
    public_view: NodePublicViewRead
    observability: NodeObservabilityRead
    warnings: list[str]
    error: WorkflowErrorRead | None
    created_at: datetime

    @model_validator(mode="after")
    def validate_typed_output(self) -> "AgentWorkflowNodeResultRead":
        """Revalidate persisted output by its discriminator before public serialization."""

        expected_model = NODE_OUTPUT_MODELS.get(self.output_type)
        if expected_model is None:
            raise ValueError("Agent node output_type is not registered")
        if self.output_schema_version != node_output_schema_version(self.output_type):
            raise ValueError("Agent node output_schema_version is not registered")
        if self.output is not None:
            if not isinstance(self.output, expected_model):
                raise ValueError("Agent node output does not match output_type")
            validate_node_output(
                self.output_type,
                self.output.model_dump(mode="json"),
            )
        return self


class AgentWorkflowSummaryRead(StrictContract):
    """Expose current workflow position including every concurrently active node."""

    workflow_execution_id: str
    run_id: str
    workflow_name: str
    workflow_version: str
    execution_profile: str
    status: AgentWorkflowStatus
    version: int
    snapshot_version: int
    current_stage: str | None
    primary_node_execution_id: str | None
    active_node_execution_ids: list[str]
    model_call_count: int
    max_model_calls: int
    node_count: int
    max_nodes: int
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime
    error: WorkflowErrorRead | None


class AgentWorkflowResultRead(AgentWorkflowSummaryRead):
    """Return one snapshot containing all bounded node parameters in sequence order."""

    nodes: list[AgentWorkflowNodeResultRead]
    final_output: FinalSynthesisOutput | None
    completion: WorkflowCompletionOutput | None
    aggregate_usage: NodeUsageRead
    warnings: list[str]
    trace_id: str | None


class AgentWorkflowEventRead(StrictContract):
    """Expose one persisted ordered business event suitable for SSE replay."""

    event_id: str
    event_sequence: int
    workflow_execution_id: str
    run_id: str
    node_execution_id: str | None
    event_type: str
    workflow_status: str
    node_status: str | None
    occurred_at: datetime
    public_summary: str
    public_payload: dict
    trace_id: str | None

    def to_sse(self) -> str:
        """Serialize this event with stable id, event name, and compact JSON data."""

        return (
            f"id: {self.event_sequence}\n"
            f"event: {self.event_type}\n"
            f"data: {self.model_dump_json()}\n\n"
        )
