"""Policy for repairing controller plans based on failure checks and resources."""


def controller_plan_repair_skip_reason(
    *,
    workflow_version: str,
    failed_check_ids: set[str],
    repair_attempts_this_round: int,
    remaining_model_calls: int,
    minimum_completion_model_calls: int,
    remaining_nodes: int,
    minimum_completion_nodes: int,
    remaining_wall_time_ms: int,
    is_cancelled: bool = False,
    outcome_unknown: bool = False,
) -> str | None:
    """Return a stable skip reason, or None when the existing repair policy permits a call.

    Callers supply downstream completion reservations excluding repair. Repair
    itself needs one model call and two nodes (Controller and plan validation).
    """
    if workflow_version != "1.1.0":
        return "unsupported_version"
    allowed = {
        "unique_task_keys",
        "task_relationships",
        "review_policy_contract",
        "workflow_limits",
    }
    if not failed_check_ids:
        return "no_failed_checks"
    if not failed_check_ids.issubset(allowed):
        return "unrepairable_checks"
    if repair_attempts_this_round != 0:
        return "repair_limit"
    if is_cancelled:
        return "cancelled"
    if outcome_unknown:
        return "outcome_unknown"
    if remaining_wall_time_ms <= 0:
        return "time_budget"
    if (
        remaining_model_calls < 0
        or remaining_nodes < 0
        or minimum_completion_model_calls < 0
        or minimum_completion_nodes < 0
    ):
        return "invalid_budget"
    if remaining_model_calls < 1 + minimum_completion_model_calls:
        return "model_call_budget"
    if remaining_nodes < 2 + minimum_completion_nodes:
        return "node_budget"
    return None


def can_repair_controller_plan(
    *,
    workflow_version: str,
    failed_check_ids: set[str],
    repair_attempts_this_round: int,
    remaining_model_calls: int,
    minimum_completion_model_calls: int,
    remaining_nodes: int,
    minimum_completion_nodes: int,
    remaining_wall_time_ms: int,
    is_cancelled: bool = False,
    outcome_unknown: bool = False,
) -> bool:
    """Keep the boolean eligibility interface using the same rules as skip-event explanations."""
    return (
        controller_plan_repair_skip_reason(
            workflow_version=workflow_version,
            failed_check_ids=failed_check_ids,
            repair_attempts_this_round=repair_attempts_this_round,
            remaining_model_calls=remaining_model_calls,
            minimum_completion_model_calls=minimum_completion_model_calls,
            remaining_nodes=remaining_nodes,
            minimum_completion_nodes=minimum_completion_nodes,
            remaining_wall_time_ms=remaining_wall_time_ms,
            is_cancelled=is_cancelled,
            outcome_unknown=outcome_unknown,
        )
        is None
    )
