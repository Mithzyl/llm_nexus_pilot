"""Check task-role instructions and the minimum budget for tool round trips."""

import pytest
from test_agent_workflows import workflow_payload

from nexuspilot_api.features.agent_runtime.schemas.agent_workflows import (
    AgentWorkflowCreate,
    ControllerPlanOutput,
)
from nexuspilot_api.features.agent_runtime.services.workflow_policy import (
    structured_model_instructions,
    validate_controller_plan,
)


@pytest.mark.parametrize("capability", ["read", "tool"])
@pytest.mark.parametrize("calls,valid", [(1, False), (2, True), (3, True)])
def test_tool_task_requires_request_and_result_calls(capability, calls, valid):
    """Reject one-call tool tasks before dispatch without raising pure-text minimums."""
    payload = AgentWorkflowCreate.model_validate(
        workflow_payload(
            workflow_version="1.1.0",
            execution_profile="tool_enabled_v1",
            max_model_calls=16,
        )
    )
    plan = ControllerPlanOutput(
        decision_summary="Read evidence",
        review_policy="always",
        tasks=[
            dict(
                task_key="read",
                task_type="analysis",
                title="Read",
                objective="Read",
                assigned_role="researcher",
                required_capabilities=[capability],
                expected_output_type="agent_handoff",
                completion_criteria=["Cite source"],
                max_model_calls=calls,
            )
        ],
    )
    result = validate_controller_plan(plan, payload)
    assert result.is_valid is valid
    if not valid:
        check = next(c for c in result.checks if c.check_id == "workflow_limits")
        assert check.status == "fail"
        assert check.related_task_keys == ["read"]
        assert "at least 2" in check.message
    plan.tasks[0].required_capabilities = ["text_analysis"]
    plan.tasks[0].max_model_calls = 1
    assert validate_controller_plan(plan, payload).is_valid


def test_controller_instructions_separate_platform_nodes_from_worker_roles():
    """Both native-schema and prompted JSON planning must constrain task role choices."""
    for prompted in (False, True):
        instructions = structured_model_instructions(
            role="controller",
            purpose="Plan",
            output_model=ControllerPlanOutput,
            prompted_json=prompted,
        )
        assert "allowed_roles" in instructions
        assert "platform" in instructions
        assert "legacy planning estimate" in instructions
        assert "not a Worker execution ceiling" in instructions


def test_model_only_call_count_is_budget_error_not_a_tool_permission_error():
    """Keep single-call text tasks mandatory but classify bad numbers as repairable limits."""
    payload = AgentWorkflowCreate.model_validate(
        workflow_payload(workflow_version="1.1.0", max_model_calls=16)
    )
    plan = ControllerPlanOutput(
        decision_summary="Text task",
        review_policy="always",
        tasks=[
            dict(
                task_key="text",
                task_type="analysis",
                title="Text",
                objective="Text",
                assigned_role="researcher",
                required_capabilities=["text_analysis"],
                expected_output_type="agent_handoff",
                completion_criteria=["Answer"],
                max_model_calls=2,
            )
        ],
    )
    result = validate_controller_plan(plan, payload)
    assert not result.is_valid
    assert [c.check_id for c in result.checks if c.status == "fail"] == ["workflow_limits"]
    assert not result.capability_gaps
