"""Tests for versioned Agent workflow and node definition registration."""

import pytest

from nexuspilot_api.features.agent_runtime.services.workflow_definition_registry import (
    AgentWorkflowDefinition,
    AgentWorkflowNodeDefinition,
    WorkflowDefinitionRegistry,
    create_default_workflow_definition_registry,
)


def test_default_model_only_definition_accepts_fixed_and_task_scoped_nodes() -> None:
    """Verify the registered workflow recognizes fixed nodes and task-specific node keys."""

    registry = create_default_workflow_definition_registry()
    definition = registry.require_definition("model_only", "1.0.0", "model_only_v1")

    definition.validate_node(
        node_key="controller_planning",
        node_type="model",
        output_type="controller_plan",
    )
    definition.validate_node(
        node_key="worker_execution.research-1",
        node_type="agent_model_execution",
        output_type="agent_model_execution",
    )
    definition.validate_transition(
        node_key="worker_execution.research-1",
        next_node_keys=["handoff_submission.research-1"],
        skipped_node_keys=[],
    )


def test_registry_rejects_duplicate_workflow_identity() -> None:
    """Verify one workflow name, version, and execution profile cannot be registered twice."""

    registry = WorkflowDefinitionRegistry()
    definition = AgentWorkflowDefinition(
        workflow_name="test_workflow",
        workflow_version="1.0.0",
        execution_profile="test_v1",
        node_definitions=(
            AgentWorkflowNodeDefinition(
                node_key_pattern="entry",
                node_type="deterministic",
                output_type="request_intake",
                allowed_next_node_patterns=(),
            ),
        ),
    )

    registry.register(definition)
    with pytest.raises(ValueError, match="already registered"):
        registry.register(definition)


def test_definition_rejects_unknown_output_type_and_transition_target() -> None:
    """Verify configuration errors fail during registration rather than during execution."""

    registry = WorkflowDefinitionRegistry()
    unknown_output_definition = AgentWorkflowDefinition(
        workflow_name="bad_output",
        workflow_version="1.0.0",
        execution_profile="bad_output_v1",
        node_definitions=(
            AgentWorkflowNodeDefinition(
                node_key_pattern="entry",
                node_type="deterministic",
                output_type="missing_output_type",
                allowed_next_node_patterns=(),
            ),
        ),
    )
    unknown_transition_definition = AgentWorkflowDefinition(
        workflow_name="bad_transition",
        workflow_version="1.0.0",
        execution_profile="bad_transition_v1",
        node_definitions=(
            AgentWorkflowNodeDefinition(
                node_key_pattern="entry",
                node_type="deterministic",
                output_type="request_intake",
                allowed_next_node_patterns=("missing_node",),
            ),
        ),
    )

    with pytest.raises(ValueError, match="unknown output type"):
        registry.register(unknown_output_definition)
    with pytest.raises(ValueError, match="unknown next-node pattern"):
        registry.register(unknown_transition_definition)


def test_definition_rejects_runtime_node_and_transition_mismatches() -> None:
    """Verify persisted node facts cannot diverge from their registered definition."""

    definition = create_default_workflow_definition_registry().require_definition(
        "model_only",
        "1.0.0",
        "model_only_v1",
    )

    with pytest.raises(ValueError, match="not registered"):
        definition.validate_node(
            node_key="unexpected_node",
            node_type="model",
            output_type="controller_plan",
        )
    with pytest.raises(ValueError, match="node type"):
        definition.validate_node(
            node_key="controller_planning",
            node_type="deterministic",
            output_type="controller_plan",
        )
    with pytest.raises(ValueError, match="transition"):
        definition.validate_transition(
            node_key="request_intake",
            next_node_keys=["final_synthesis"],
            skipped_node_keys=[],
        )
    with pytest.raises(ValueError, match="different task keys"):
        definition.validate_transition(
            node_key="worker_execution.research-1",
            next_node_keys=["handoff_submission.research-2"],
            skipped_node_keys=[],
        )
