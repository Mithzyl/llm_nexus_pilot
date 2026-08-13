export type SessionStatus = "active" | "archived";
export type MessageRole = "system" | "user" | "assistant" | "tool";
export type RunStatus =
  | "pending"
  | "running"
  | "completed"
  | "failed"
  | "cancel_requested"
  | "cancelled";

export type ProviderName =
  | "openai"
  | "deepseek"
  | "anthropic"
  | "gemini"
  | "openai_compatible";

export type ProviderCatalog = {
  providers: ProviderName[];
  models_by_provider: Partial<Record<ProviderName, string[]>>;
};

export type CursorPage<T> = {
  items: T[];
  next_cursor: string | null;
  has_more: boolean;
  limit: number;
};

export type Session = {
  session_id: string;
  user_id: string;
  title: string | null;
  status: SessionStatus;
  created_at: string;
  updated_at: string;
};

export type MessageSummary = {
  message_id: string;
  session_id: string;
  run_id: string | null;
  parent_message_id: string | null;
  role: MessageRole;
  content_type: string;
  content_preview: string | null;
  content_uri: string | null;
  sequence: number;
  token_count: number | null;
  created_at: string;
};

export type Message = Omit<MessageSummary, "content_preview"> & {
  content_text: string | null;
  metadata_json: Record<string, unknown> | null;
};

export type Run = {
  run_id: string;
  user_id: string;
  session_id: string | null;
  user_request: string;
  run_type: string;
  status: RunStatus;
  budget_limit: string | number | null;
  cost_used: string | number;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
  updated_at: string;
};

export type ModelAttempt = {
  attempt_id: string;
  run_id: string;
  task_id: string | null;
  provider: string;
  model: string;
  request_type: string;
  retry_count: number;
  status: "started" | "completed" | "failed" | "timed_out" | "cancelled";
  input_tokens: number | null;
  output_tokens: number | null;
  cached_tokens: number | null;
  estimated_cost: string | number | null;
  latency_ms: number | null;
  error_code: string | null;
  started_at: string;
  completed_at: string | null;
};

export type RunDetail = Run & {
  tasks: Array<Record<string, unknown>>;
  attempts: ModelAttempt[];
  artifacts: Array<Record<string, unknown>>;
  tasks_has_more: boolean;
  attempts_has_more: boolean;
  artifacts_has_more: boolean;
};

export type ResponseUsage = {
  input_tokens: number | null;
  output_tokens: number | null;
  cached_tokens: number | null;
  estimated_cost: string | null;
};

export type ResponseResult = {
  id: string;
  object: "response";
  status: "completed";
  provider: ProviderName;
  model: string;
  output_text: string | null;
  tool_calls: Array<Record<string, unknown>>;
  structured_output: Record<string, unknown> | Array<unknown> | null;
  finish_reason: string;
  usage: ResponseUsage;
  latency_ms: number;
  provider_request_id: string | null;
};

export type StreamEvent = {
  type: string;
  sequence: number;
  data: {
    delta?: string;
    attempt_id?: string;
    response?: ResponseResult;
    usage?: ResponseUsage;
    error?: { type?: string; message?: string };
    [key: string]: unknown;
  };
};

export type AgentWorkflowStatus =
  | "pending"
  | "running"
  | "waiting_for_input"
  | "completed"
  | "failed"
  | "cancelled"
  | "outcome_unknown";

export type AgentWorkflowNodeStatus =
  | "pending"
  | "running"
  | "completed"
  | "skipped"
  | "blocked"
  | "failed"
  | "cancelled"
  | "outcome_unknown";

export type AgentWorkflowEventType =
  | "agent.workflow.started"
  | "agent.workflow.completed"
  | "agent.workflow.failed"
  | "agent.workflow.cancelled"
  | "agent.workflow.outcome_unknown"
  | "agent.node.started"
  | "agent.node.completed"
  | "agent.node.failed"
  | "agent.node.cancelled"
  | "agent.node.outcome_unknown";

export type AgentModelBinding = {
  provider: ProviderName;
  model: string;
  structured_output_mode?: "native_schema" | "prompted_json";
  timeout_seconds?: number;
  max_output_tokens?: number;
};

export type AgentWorkflowCreate = {
  workflow_name: "model_only";
  workflow_version: "1.0.0";
  execution_profile: "model_only_v1";
  idempotency_key: string;
  role_bindings: Partial<
    Record<"controller" | "planner" | "researcher" | "reviewer" | "verifier", AgentModelBinding>
  >;
  review_policy: "always" | "on_verification_failure" | "never";
  max_nodes: number;
  max_model_calls: number;
  max_parallel_agents: 1 | 2;
  wall_time_limit_ms: number;
  stream: boolean;
};

export type AgentWorkflowError = {
  error_code: string;
  error_type: string;
  public_message: string;
  is_retryable: boolean;
  outcome_is_known: boolean;
  details_reference_id: string | null;
};

export type AgentWorkflowUsage = {
  input_tokens: number;
  output_tokens: number;
  cached_tokens: number;
  estimated_cost: string | number;
  model_call_count: number;
  tool_call_count: number;
};

export type AgentWorkflowSummary = {
  workflow_execution_id: string;
  run_id: string;
  workflow_name: string;
  workflow_version: string;
  execution_profile: string;
  status: AgentWorkflowStatus;
  version: number;
  snapshot_version: number;
  current_stage: string | null;
  primary_node_execution_id: string | null;
  active_node_execution_ids: string[];
  model_call_count: number;
  max_model_calls: number;
  node_count: number;
  max_nodes: number;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
  updated_at: string;
  error: AgentWorkflowError | null;
};

export type AgentWorkflowNodeOutputType =
  | "request_intake"
  | "context_assembly"
  | "controller_plan"
  | "plan_validation"
  | "agent_dispatch"
  | "agent_model_execution"
  | "agent_handoff"
  | "deterministic_verification"
  | "independent_review"
  | "final_synthesis"
  | "workflow_completion";

export type AgentWorkflowNodeOutputMap = {
  request_intake: {
    normalized_objective: string;
    request_type: string;
    complexity: "simple" | "complex";
    requires_decomposition: boolean;
    constraints: string[];
    acceptance_criteria: string[];
    explicit_assumptions: string[];
    clarification_questions: string[];
    required_capabilities: string[];
    unavailable_capabilities: string[];
    requested_output_format: string;
    language: string;
  };
  context_assembly: {
    included_run_id: string;
    included_message_ids: string[];
    included_artifact_ids: string[];
    included_handoff_ids: string[];
    excluded_sources: Array<{ source_type: string; source_id: string | null; reason: string }>;
    input_character_count: number;
    is_truncated: boolean;
    memory_packet_id: null;
  };
  controller_plan: {
    decision_summary: string;
    tasks: Array<{
      task_key: string;
      task_type: string;
      title: string;
      objective: string;
      assigned_role: string;
      required_capabilities: string[];
      expected_output_type: "agent_handoff";
      completion_criteria: string[];
      priority: number;
      timeout_seconds: number;
      max_model_calls: 1;
    }>;
    dependencies: Array<{
      task_key: string;
      depends_on_task_key: string;
      dependency_type: "completion";
    }>;
    review_policy: "always" | "on_verification_failure" | "never";
    known_risks: string[];
    unknowns: string[];
  };
  plan_validation: {
    is_valid: boolean;
    checks: Array<{
      check_id: string;
      status: "pass" | "fail";
      error_code: string | null;
      message: string;
      related_task_keys: string[];
    }>;
    topological_task_order: string[];
    cycle_paths: string[][];
    total_task_count: number;
    total_model_call_limit: number;
    capability_gaps: Array<Record<string, string>>;
    relationship_errors: string[];
    rejected_plan_reason: string | null;
  };
  agent_dispatch: {
    created_tasks: Array<{ task_key: string; task_id: string; status: string }>;
    created_agent_runs: Array<{
      task_id: string;
      agent_run_id: string;
      agent_role: string;
      provider: ProviderName;
      model: string;
      status: string;
    }>;
    dispatch_groups: Array<{ group_id: string; agent_run_ids: string[]; concurrency_limit: 1 | 2 }>;
    blocked_tasks: Array<{
      task_key: string;
      task_id: string | null;
      reason_code: string;
      summary: string;
    }>;
    skipped_tasks: Array<{ task_key: string; reason_code: string }>;
    role_model_bindings: Array<{ agent_role: string; provider: ProviderName; model: string }>;
  };
  agent_model_execution: {
    agent_turn_id: string;
    model_attempt_id: string;
    provider: ProviderName;
    model: string;
    response_kind: "structured";
    text_preview: string | null;
    text_artifact_id: string | null;
    structured_output: {
      summary: string;
      confirmed_facts: string[];
      decisions: string[];
      remaining_work: string[];
      risks: string[];
      unknowns: string[];
    };
    requested_tool_calls: [];
    finish_reason: string;
    provider_request_id: string | null;
    input_tokens: number | null;
    output_tokens: number | null;
    cached_tokens: number | null;
    estimated_cost: string | number | null;
    latency_ms: number;
    transport_attempt_count: number;
    capability_warnings: string[];
  };
  agent_handoff: {
    agent_handoff_id: string;
    schema_version: "agent_handoff.v1";
    status: "completed" | "partial";
    objective: string;
    confirmed_facts: string[];
    decisions: string[];
    files_read: string[];
    files_changed: string[];
    artifacts: string[];
    tests: string[];
    remaining_work: string[];
    risks: string[];
    unknowns: string[];
    invariants_for_next_agent: string[];
    supersedes_handoff_id: string | null;
  };
  deterministic_verification: {
    verdict: "pass" | "fail" | "unknown";
    checks: Array<{
      check_id: string;
      check_type: string;
      status: "pass" | "fail" | "unknown";
      expected: string;
      actual: string;
      evidence_refs: string[];
      error_code: string | null;
    }>;
    blocking_findings: string[];
    non_blocking_findings: string[];
    verified_claims: string[];
    unverified_claims: string[];
    coverage_summary: string;
    requires_independent_review: boolean;
  };
  independent_review: {
    verdict: "pass" | "fail" | "needs_revision";
    score: string | number;
    findings: Array<{
      finding_id: string;
      severity: "error" | "warning" | "info";
      category: string;
      description: string;
      evidence_refs: string[];
      required_action: string | null;
    }>;
    accepted_claims: string[];
    rejected_claims: string[];
    missing_evidence: string[];
    requires_replan: boolean;
    requires_retry: boolean;
    review_summary: string;
    evaluation_ids: string[];
    reviewer_task_id: string;
    reviewer_agent_run_id: string;
    reviewer_model_attempt_id: string;
  };
  final_synthesis: {
    answer_type: "text";
    final_text: string;
    completed_objectives: string[];
    unresolved_items: string[];
    warnings: string[];
    recommended_next_actions: string[];
    source_agent_run_ids: string[];
    source_handoff_ids: string[];
    source_artifact_ids: string[];
    source_evaluation_ids: string[];
    assistant_message_id: string | null;
  };
  workflow_completion: {
    workflow_status: "completed";
    final_node_execution_id: string;
    node_count: number;
    node_counts_by_status: Record<string, number>;
    task_ids: string[];
    agent_run_ids: string[];
    model_attempt_ids: string[];
    tool_call_ids: string[];
    evaluation_ids: string[];
    artifact_ids: string[];
    handoff_ids: string[];
    total_input_tokens: number;
    total_output_tokens: number;
    total_cached_tokens: number;
    total_estimated_cost: string | number;
    total_duration_ms: number;
    remaining_model_calls: number;
    final_output_reference: string;
    warnings: string[];
    errors: AgentWorkflowError[];
  };
};

type AgentWorkflowNodeBase = {
  schema_version: "agent_workflow_node_result.v1";
  workflow_execution_id: string;
  workflow_name: string;
  workflow_version: string;
  node_execution_id: string;
  node_key: string;
  node_type: string;
  node_version: string;
  node_sequence: number;
  node_attempt: number;
  parent_node_execution_id: string | null;
  run_id: string;
  task_id: string | null;
  agent_run_id: string | null;
  agent_turn_id: string | null;
  agent_role: string | null;
  status: AgentWorkflowNodeStatus;
  input: {
    schema_version: string;
    input_hash: string;
    source_node_execution_ids: string[];
    message_ids: string[];
    artifact_ids: string[];
    handoff_ids: string[];
    context_build_id: string | null;
    prompt_release_id: string | null;
  };
  transition: {
    selected_transition: string | null;
    next_node_keys: string[];
    skipped_node_keys: string[];
    condition_summary: string;
  };
  evidence: {
    model_attempt_ids: string[];
    tool_call_ids: string[];
    evaluation_ids: string[];
    artifact_ids: string[];
    handoff_ids: string[];
  };
  usage: AgentWorkflowUsage;
  budget: {
    cost_limit: string | number | null;
    cost_used_before: string | number;
    cost_used_after: string | number;
    reserved_estimated_cost: string | number;
    remaining_model_calls: number;
    remaining_nodes: number;
    remaining_wall_time_ms: number;
    remaining_cost: string | number | null;
  };
  timing: { started_at: string; completed_at: string | null; duration_ms: number };
  public_view: {
    status_label: string;
    summary: string;
    progress_current: number;
    progress_total: number;
  };
  observability: { trace_id: string | null; span_id: string | null };
  warnings: string[];
  error: AgentWorkflowError | null;
  created_at: string;
};

export type AgentWorkflowNodeResult = {
  [OutputType in AgentWorkflowNodeOutputType]: AgentWorkflowNodeBase & {
    output_type: OutputType;
    output_schema_version: string;
    output: AgentWorkflowNodeOutputMap[OutputType] | null;
  };
}[AgentWorkflowNodeOutputType];

export type AgentWorkflowResult = AgentWorkflowSummary & {
  nodes: AgentWorkflowNodeResult[];
  final_output: AgentWorkflowNodeOutputMap["final_synthesis"] | null;
  completion: AgentWorkflowNodeOutputMap["workflow_completion"] | null;
  aggregate_usage: AgentWorkflowUsage;
  warnings: string[];
  trace_id: string | null;
};

export type AgentWorkflowEvent = {
  event_id: string;
  event_sequence: number;
  workflow_execution_id: string;
  run_id: string;
  node_execution_id: string | null;
  event_type: AgentWorkflowEventType;
  workflow_status: AgentWorkflowStatus;
  node_status: AgentWorkflowNodeStatus | null;
  occurred_at: string;
  public_summary: string;
  public_payload: Record<string, unknown>;
  trace_id: string | null;
};
