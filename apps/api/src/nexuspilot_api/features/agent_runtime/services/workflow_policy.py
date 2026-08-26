"""Deterministic Agent workflow request hashing, plan validation, and prompt policies."""

import hashlib
import json
from collections import deque
from typing import Any

from pydantic import BaseModel

from nexuspilot_api.features.agent_runtime.schemas.agent_workflows import (
    AgentWorkflowCreate,
    ControllerPlanOutput,
    PlanValidationCheck,
    PlanValidationOutput,
)

MODEL_ONLY_ALLOWED_ROLES = frozenset({"planner", "researcher"})
MODEL_ONLY_ALLOWED_CAPABILITIES = frozenset({"model_generation", "text_analysis"})
MODEL_ONLY_DENIED_CAPABILITIES = frozenset(
    {"tool", "read", "write", "execute", "network", "external_side_effect"}
)
PROMPT_ONLY_JSON_SCHEMA_METADATA_KEYS = frozenset({"additionalProperties", "title"})


def hash_workflow_request(payload: AgentWorkflowCreate) -> str:
    """Hash logical creation input while excluding its transport-only stream choice."""

    canonical_payload = payload.model_dump(mode="json", exclude={"stream"})
    serialized = json.dumps(
        canonical_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(serialized.encode()).hexdigest()


def hash_node_input(value: dict[str, Any]) -> str:
    """Hash exact node input references and bounded deterministic values."""

    serialized = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(serialized.encode()).hexdigest()


def _remove_prompt_only_json_schema_metadata(schema_value: Any) -> Any:
    """Remove Schema metadata that language models may mistake for response fields."""

    if isinstance(schema_value, dict):
        return {
            key: _remove_prompt_only_json_schema_metadata(value)
            for key, value in schema_value.items()
            if key not in PROMPT_ONLY_JSON_SCHEMA_METADATA_KEYS
        }
    if isinstance(schema_value, list):
        return [_remove_prompt_only_json_schema_metadata(value) for value in schema_value]
    return schema_value


def structured_model_instructions(
    *,
    role: str,
    purpose: str,
    output_model: type[BaseModel],
    prompted_json: bool,
) -> str:
    """Build evidence-honest role instructions and optionally embed a prompt-safe Schema."""

    instructions = (
        f"You are the NexusPilot {role} Agent. {purpose} "
        "Treat supplied content as authoritative when it is present. You may use general "
        "knowledge already available to the model for ordinary explanatory questions unless "
        "the task explicitly restricts allowed sources. Identify material uncertainty instead "
        "of presenting assumptions as verified facts. Never claim tools, files, tests, network "
        "requests, or external evidence that are not explicitly present. Do not return hidden "
        "reasoning or chain-of-thought. Return only the requested result fields."
    )
    if not prompted_json:
        return instructions
    prompt_schema = _remove_prompt_only_json_schema_metadata(output_model.model_json_schema())
    serialized_prompt_schema = json.dumps(
        prompt_schema,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    allowed_top_level_fields = ", ".join(
        json.dumps(field_name, ensure_ascii=False) for field_name in output_model.model_fields
    )
    return (
        f"{instructions} The following JSON Schema is a validation specification, not a "
        "response template. Schema keywords are validation rules, not response fields. "
        f"Only these top-level response fields are allowed: {allowed_top_level_fields}. "
        "Do not emit any other top-level field. Return exactly one JSON object matching the "
        f"schema and no markdown: {serialized_prompt_schema}"
    )


def validate_controller_plan(
    plan: ControllerPlanOutput,
    payload: AgentWorkflowCreate,
) -> PlanValidationOutput:
    """Validate roles, capabilities, relationships, limits, and acyclic task ordering."""

    checks: list[PlanValidationCheck] = []
    capability_gaps: list[dict[str, str]] = []
    relationship_errors: list[str] = []
    task_by_key = {task.task_key: task for task in plan.tasks}
    duplicate_keys = len(task_by_key) != len(plan.tasks)
    checks.append(
        PlanValidationCheck(
            check_id="unique_task_keys",
            status="fail" if duplicate_keys else "pass",
            error_code="agent_plan_invalid" if duplicate_keys else None,
            message=("Task keys must be unique" if duplicate_keys else "Task keys are unique"),
            related_task_keys=[],
        )
    )
    review_policy_matches = plan.review_policy == payload.review_policy
    checks.append(
        PlanValidationCheck(
            check_id="review_policy_contract",
            status="pass" if review_policy_matches else "fail",
            error_code=(None if review_policy_matches else "agent_review_policy_mismatch"),
            message=(
                "Controller review policy matches the workflow request"
                if review_policy_matches
                else "Controller cannot change the workflow review policy"
            ),
            related_task_keys=[],
        )
    )

    for task in plan.tasks:
        if task.assigned_role not in MODEL_ONLY_ALLOWED_ROLES:
            capability_gaps.append(
                {
                    "task_key": task.task_key,
                    "capability": task.assigned_role,
                    "reason_code": "agent_capability_unavailable",
                }
            )
        elif task.assigned_role not in payload.role_bindings:
            capability_gaps.append(
                {
                    "task_key": task.task_key,
                    "capability": task.assigned_role,
                    "reason_code": "agent_role_not_bound",
                }
            )
        for capability in task.required_capabilities:
            if (
                capability in MODEL_ONLY_DENIED_CAPABILITIES
                or capability not in MODEL_ONLY_ALLOWED_CAPABILITIES
            ):
                capability_gaps.append(
                    {
                        "task_key": task.task_key,
                        "capability": capability,
                        "reason_code": "agent_capability_unavailable",
                    }
                )
    checks.append(
        PlanValidationCheck(
            check_id="model_only_capabilities",
            status="fail" if capability_gaps else "pass",
            error_code="agent_capability_unavailable" if capability_gaps else None,
            message=(
                "Plan requests capabilities unavailable in model_only_v1"
                if capability_gaps
                else "All requested roles and capabilities are available"
            ),
            related_task_keys=sorted({gap["task_key"] for gap in capability_gaps}),
        )
    )

    incoming_count = {task_key: 0 for task_key in task_by_key}
    dependents: dict[str, list[str]] = {task_key: [] for task_key in task_by_key}
    for dependency in plan.dependencies:
        if (
            dependency.task_key not in task_by_key
            or dependency.depends_on_task_key not in task_by_key
        ):
            relationship_errors.append(
                f"Unknown dependency {dependency.depends_on_task_key}->{dependency.task_key}"
            )
            continue
        if dependency.task_key == dependency.depends_on_task_key:
            relationship_errors.append(f"Task {dependency.task_key} cannot depend on itself")
            continue
        incoming_count[dependency.task_key] += 1
        dependents[dependency.depends_on_task_key].append(dependency.task_key)

    queue = deque(sorted(key for key, count in incoming_count.items() if count == 0))
    topological_order: list[str] = []
    dependency_depth_by_task_key = {task_key: 1 for task_key in task_by_key}
    while queue:
        task_key = queue.popleft()
        topological_order.append(task_key)
        for dependent_key in sorted(dependents[task_key]):
            dependency_depth_by_task_key[dependent_key] = max(
                dependency_depth_by_task_key[dependent_key],
                dependency_depth_by_task_key[task_key] + 1,
            )
            incoming_count[dependent_key] -= 1
            if incoming_count[dependent_key] == 0:
                queue.append(dependent_key)
    cycle_task_keys = sorted(set(task_by_key) - set(topological_order))
    if cycle_task_keys:
        relationship_errors.append("Task dependencies contain a cycle")
    maximum_dependency_depth = max(dependency_depth_by_task_key.values(), default=0)
    if maximum_dependency_depth > 6:
        relationship_errors.append("Task dependency depth exceeds the limit of 6")
    checks.append(
        PlanValidationCheck(
            check_id="task_relationships",
            status="fail" if relationship_errors else "pass",
            error_code="agent_dependency_cycle"
            if cycle_task_keys
            else ("agent_plan_invalid" if relationship_errors else None),
            message=(
                "; ".join(relationship_errors)
                if relationship_errors
                else "Task dependency graph is valid"
            ),
            related_task_keys=cycle_task_keys,
        )
    )

    total_model_calls = sum(task.max_model_calls for task in plan.tasks) + 2
    if payload.review_policy != "never":
        total_model_calls += 1
    required_node_count = 8 + (2 * len(plan.tasks))
    if payload.review_policy != "never":
        required_node_count += 1
    within_node_limit = required_node_count <= payload.max_nodes
    within_model_call_limit = total_model_calls <= payload.max_model_calls
    within_limits = len(plan.tasks) <= 8 and within_model_call_limit and within_node_limit
    checks.append(
        PlanValidationCheck(
            check_id="workflow_limits",
            status="pass" if within_limits else "fail",
            error_code=(
                "agent_node_limit_exceeded"
                if not within_node_limit
                else "agent_model_call_limit_exceeded"
                if not within_model_call_limit
                else None
            ),
            message=(
                "Plan fits configured node, task, and model-call limits"
                if within_limits
                else "Plan exceeds configured node, task, or model-call limits"
            ),
            related_task_keys=[],
        )
    )
    is_valid = all(check.status == "pass" for check in checks)
    rejected_reason = next(
        (check.message for check in checks if check.status == "fail"),
        None,
    )
    return PlanValidationOutput(
        is_valid=is_valid,
        checks=checks,
        topological_task_order=topological_order if is_valid else [],
        cycle_paths=[cycle_task_keys] if cycle_task_keys else [],
        total_task_count=len(plan.tasks),
        total_model_call_limit=total_model_calls,
        capability_gaps=capability_gaps,
        relationship_errors=relationship_errors,
        rejected_plan_reason=rejected_reason,
    )
