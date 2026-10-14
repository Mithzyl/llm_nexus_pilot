"""Tests for the controller plan repair policy."""

import pytest

from nexuspilot_api.features.agent_runtime.services.controller_plan_repair_policy import (
    can_repair_controller_plan,
)

_VALID_KWARGS = {
    "workflow_version": "1.1.0",
    "failed_check_ids": {"unique_task_keys"},
    "repair_attempts_this_round": 0,
    "remaining_model_calls": 10,
    "minimum_completion_model_calls": 5,
    "remaining_nodes": 10,
    "minimum_completion_nodes": 5,
    "remaining_wall_time_ms": 1000,
}


def _valid(**overrides: object) -> dict:
    """Return valid kwargs with optional overrides."""
    base = dict(_VALID_KWARGS)
    base.update(overrides)
    return base


class TestAllowedCases:
    """Test cases that should return True."""

    @pytest.mark.parametrize(
        "check_id",
        ["unique_task_keys", "task_relationships", "review_policy_contract", "workflow_limits"],
    )
    def test_single_allowed_failure(self, check_id: str) -> None:
        """Single allowed failure key passes."""
        assert can_repair_controller_plan(**_valid(failed_check_ids={check_id}))

    def test_mixed_allowed_failures(self) -> None:
        """Mixed allowed failure keys pass."""
        assert can_repair_controller_plan(
            **_valid(failed_check_ids={"unique_task_keys", "workflow_limits"})
        )

    def test_exact_resource_boundary(self) -> None:
        """Exact boundary values pass."""
        assert can_repair_controller_plan(
            **_valid(
                remaining_model_calls=6,
                remaining_nodes=7,
            )
        )


class TestInvalidVersion:
    """Test invalid workflow versions."""

    @pytest.mark.parametrize("version", ["1.0.0", "2.0.0"], ids=["old", "unknown"])
    def test_invalid_version(self, version: str) -> None:
        """Old or unknown versions fail."""
        assert not can_repair_controller_plan(**_valid(workflow_version=version))


class TestInvalidFailures:
    """Test invalid failed_check_ids."""

    def test_empty_failures(self) -> None:
        """Empty failure set fails."""
        assert not can_repair_controller_plan(**_valid(failed_check_ids=set()))

    def test_unknown_failure(self) -> None:
        """Reject checks not explicitly authorized for business plan repair."""
        assert not can_repair_controller_plan(**_valid(failed_check_ids={"unknown_check"}))

    def test_forbidden_mixed_with_allowed(self) -> None:
        """Forbidden key mixed with allowed fails."""
        assert not can_repair_controller_plan(
            **_valid(failed_check_ids={"model_only_capabilities", "unique_task_keys"})
        )


class TestInvalidRepairs:
    """Test invalid repair attempts."""

    @pytest.mark.parametrize("repairs", [1, 2, -1], ids=["used", "over_limit", "negative"])
    def test_invalid_repairs(self, repairs: int) -> None:
        """Non-zero or negative repair attempts fail."""
        assert not can_repair_controller_plan(**_valid(repair_attempts_this_round=repairs))


class TestInvalidFlags:
    """Test invalid cancellation/unknown flags."""

    @pytest.mark.parametrize(
        "is_cancelled, outcome_unknown",
        [
            (True, False),
            (False, True),
        ],
        ids=["cancelled", "unknown_outcome"],
    )
    def test_invalid_flags(self, is_cancelled: bool, outcome_unknown: bool) -> None:
        """Cancelled or unknown outcome flags fail."""
        assert not can_repair_controller_plan(
            **_valid(
                is_cancelled=is_cancelled,
                outcome_unknown=outcome_unknown,
            )
        )


class TestResourceBoundaries:
    """Test resource count boundaries."""

    def test_one_short_calls(self) -> None:
        """One unit short on model calls fails."""
        assert not can_repair_controller_plan(**_valid(remaining_model_calls=5))

    def test_one_short_nodes(self) -> None:
        """One unit short on nodes fails."""
        assert not can_repair_controller_plan(**_valid(remaining_nodes=6))

    def test_zero_time(self) -> None:
        """Zero remaining wall time fails."""
        assert not can_repair_controller_plan(**_valid(remaining_wall_time_ms=0))

    def test_negative_remaining_nodes(self) -> None:
        """Negative remaining nodes fail."""
        assert not can_repair_controller_plan(**_valid(remaining_nodes=-1))

    def test_negative_remaining_calls(self) -> None:
        """Reject negative remaining model calls rather than treating them as unlimited."""
        assert not can_repair_controller_plan(**_valid(remaining_model_calls=-1))

    def test_negative_remaining_time(self) -> None:
        """Negative remaining time fails."""
        assert not can_repair_controller_plan(**_valid(remaining_wall_time_ms=-100))

    def test_negative_minimum_calls(self) -> None:
        """Negative minimum model calls fail."""
        assert not can_repair_controller_plan(**_valid(minimum_completion_model_calls=-1))

    def test_negative_minimum_nodes(self) -> None:
        """Negative minimum nodes fail."""
        assert not can_repair_controller_plan(**_valid(minimum_completion_nodes=-1))
