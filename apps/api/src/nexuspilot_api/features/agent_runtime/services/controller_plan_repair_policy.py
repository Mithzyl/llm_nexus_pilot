"""Policy for repairing controller plans based on failure checks and resources."""


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
    """Check repair eligibility without executing or persisting a model call.

    Callers supply downstream completion reservations excluding repair. Repair
    itself needs one model call and two nodes (Controller and plan validation).
    """
    if workflow_version != "1.1.0":
        return False
    allowed = {
        "unique_task_keys",
        "task_relationships",
        "review_policy_contract",
        "workflow_limits",
    }
    if not failed_check_ids or not failed_check_ids.issubset(allowed):
        return False
    if repair_attempts_this_round != 0:
        return False
    if is_cancelled or outcome_unknown:
        return False
    if remaining_wall_time_ms <= 0:
        return False
    if (
        remaining_model_calls < 0
        or remaining_nodes < 0
        or minimum_completion_model_calls < 0
        or minimum_completion_nodes < 0
    ):
        return False
    if remaining_model_calls < 1 + minimum_completion_model_calls:
        return False
    if remaining_nodes < 2 + minimum_completion_nodes:
        return False
    return True
