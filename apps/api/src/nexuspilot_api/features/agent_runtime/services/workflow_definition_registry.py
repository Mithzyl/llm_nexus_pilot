"""Register versioned Agent workflows, nodes, outputs, and allowed connections."""

from dataclasses import dataclass

from nexuspilot_api.features.agent_runtime.schemas.agent_workflows import NODE_OUTPUT_MODELS

TASK_KEY_PLACEHOLDER = "{task_key}"


@dataclass(frozen=True)
class AgentWorkflowNodeDefinition:
    """Describe one fixed or task-specific node in a versioned workflow."""

    node_key_pattern: str
    node_type: str
    output_type: str
    allowed_next_node_patterns: tuple[str, ...]

    def matches_node_key(self, node_key: str) -> bool:
        """Return whether a persisted node key belongs to this definition."""

        if TASK_KEY_PLACEHOLDER not in self.node_key_pattern:
            return node_key == self.node_key_pattern
        return self.task_key_from_node_key(node_key) is not None

    def task_key_from_node_key(self, node_key: str) -> str | None:
        """Return the task key represented by a task-specific node, when it matches."""

        if TASK_KEY_PLACEHOLDER not in self.node_key_pattern:
            return None
        prefix, suffix = self.node_key_pattern.split(TASK_KEY_PLACEHOLDER, maxsplit=1)
        if not node_key.startswith(prefix) or not node_key.endswith(suffix):
            return None
        task_key_end = len(node_key) - len(suffix) if suffix else len(node_key)
        task_key = node_key[len(prefix) : task_key_end]
        return task_key or None


@dataclass(frozen=True)
class AgentWorkflowDefinition:
    """Describe all nodes and connections for one immutable workflow version."""

    workflow_name: str
    workflow_version: str
    execution_profile: str
    node_definitions: tuple[AgentWorkflowNodeDefinition, ...]

    @property
    def identity(self) -> tuple[str, str, str]:
        """Return the registry key that uniquely identifies this workflow definition."""

        return self.workflow_name, self.workflow_version, self.execution_profile

    def validate_configuration(self) -> None:
        """Reject duplicate patterns, unsupported outputs, and unknown connection targets."""

        if not self.node_definitions:
            raise ValueError("Agent workflow definition must contain at least one node")
        patterns = [definition.node_key_pattern for definition in self.node_definitions]
        if len(patterns) != len(set(patterns)):
            raise ValueError("Agent workflow definition contains duplicate node-key patterns")
        known_patterns = set(patterns)
        for definition in self.node_definitions:
            if not definition.node_key_pattern.strip():
                raise ValueError("Agent workflow node-key pattern must not be empty")
            if definition.node_key_pattern.count(TASK_KEY_PLACEHOLDER) > 1:
                raise ValueError(
                    "Agent workflow node-key pattern has multiple task-key placeholders"
                )
            if "{" in definition.node_key_pattern.replace(TASK_KEY_PLACEHOLDER, ""):
                raise ValueError("Agent workflow node-key pattern contains an unknown placeholder")
            if definition.output_type not in NODE_OUTPUT_MODELS:
                raise ValueError(
                    f"Agent workflow node uses unknown output type {definition.output_type}"
                )
            for target_pattern in definition.allowed_next_node_patterns:
                if target_pattern not in known_patterns:
                    raise ValueError(
                        f"Agent workflow node references unknown next-node pattern {target_pattern}"
                    )

    def validate_node(self, *, node_key: str, node_type: str, output_type: str) -> None:
        """Verify one runtime node matches its registered type and output contract."""

        definition = self._require_node_definition(node_key)
        if definition.node_type != node_type:
            raise ValueError(
                f"Agent workflow node type for {node_key} must be {definition.node_type}"
            )
        if definition.output_type != output_type:
            raise ValueError(
                f"Agent workflow output type for {node_key} must be {definition.output_type}"
            )

    def validate_transition(
        self,
        *,
        node_key: str,
        next_node_keys: list[str],
        skipped_node_keys: list[str],
    ) -> None:
        """Verify all declared next or skipped nodes are allowed from the source node."""

        source_definition = self._require_node_definition(node_key)
        source_task_key = source_definition.task_key_from_node_key(node_key)
        for target_node_key in [*next_node_keys, *skipped_node_keys]:
            target_definition = self._require_node_definition(target_node_key)
            if target_definition.node_key_pattern not in (
                source_definition.allowed_next_node_patterns
            ):
                raise ValueError(
                    f"Agent workflow transition {node_key} -> {target_node_key} is not allowed"
                )
            target_task_key = target_definition.task_key_from_node_key(target_node_key)
            if (
                source_task_key is not None
                and target_task_key is not None
                and source_task_key != target_task_key
            ):
                raise ValueError(
                    "Agent workflow transition cannot connect nodes with different task keys: "
                    f"{node_key} -> {target_node_key}"
                )

    def _require_node_definition(self, node_key: str) -> AgentWorkflowNodeDefinition:
        """Return the only registered node definition matching a persisted node key."""

        matches = [
            definition
            for definition in self.node_definitions
            if definition.matches_node_key(node_key)
        ]
        if not matches:
            raise ValueError(f"Agent workflow node {node_key} is not registered")
        if len(matches) > 1:
            raise ValueError(f"Agent workflow node {node_key} matches multiple definitions")
        return matches[0]


class WorkflowDefinitionRegistry:
    """Store immutable workflow definitions under stable name, version, and profile keys."""

    def __init__(self) -> None:
        """Create an empty registry that must be populated before execution."""

        self._definition_by_identity: dict[tuple[str, str, str], AgentWorkflowDefinition] = {}

    def register(self, definition: AgentWorkflowDefinition) -> None:
        """Validate and register one definition, rejecting an existing identical key."""

        definition.validate_configuration()
        if definition.identity in self._definition_by_identity:
            raise ValueError(
                "Agent workflow definition is already registered for "
                f"{definition.workflow_name} {definition.workflow_version} "
                f"{definition.execution_profile}"
            )
        self._definition_by_identity[definition.identity] = definition

    def require_definition(
        self,
        workflow_name: str,
        workflow_version: str,
        execution_profile: str,
    ) -> AgentWorkflowDefinition:
        """Return a registered definition or reject an unsupported workflow identity."""

        identity = workflow_name, workflow_version, execution_profile
        definition = self._definition_by_identity.get(identity)
        if definition is None:
            raise ValueError(
                "Agent workflow definition is not registered for "
                f"{workflow_name} {workflow_version} {execution_profile}"
            )
        return definition


def create_default_workflow_definition_registry() -> WorkflowDefinitionRegistry:
    """Create the registry containing the current model_only_v1 workflow definition."""

    registry = WorkflowDefinitionRegistry()
    registry.register(
        AgentWorkflowDefinition(
            workflow_name="model_only",
            workflow_version="1.0.0",
            execution_profile="model_only_v1",
            node_definitions=(
                AgentWorkflowNodeDefinition(
                    "request_intake",
                    "deterministic",
                    "request_intake",
                    ("context_assembly",),
                ),
                AgentWorkflowNodeDefinition(
                    "context_assembly",
                    "context",
                    "context_assembly",
                    ("controller_planning",),
                ),
                AgentWorkflowNodeDefinition(
                    "controller_planning",
                    "model",
                    "controller_plan",
                    ("plan_validation",),
                ),
                AgentWorkflowNodeDefinition(
                    "plan_validation",
                    "deterministic",
                    "plan_validation",
                    ("agent_dispatch",),
                ),
                AgentWorkflowNodeDefinition(
                    "agent_dispatch",
                    "dispatch",
                    "agent_dispatch",
                    (f"worker_execution.{TASK_KEY_PLACEHOLDER}",),
                ),
                AgentWorkflowNodeDefinition(
                    f"worker_execution.{TASK_KEY_PLACEHOLDER}",
                    "agent_model_execution",
                    "agent_model_execution",
                    (f"handoff_submission.{TASK_KEY_PLACEHOLDER}",),
                ),
                AgentWorkflowNodeDefinition(
                    f"handoff_submission.{TASK_KEY_PLACEHOLDER}",
                    "handoff",
                    "agent_handoff",
                    ("deterministic_verification",),
                ),
                AgentWorkflowNodeDefinition(
                    "deterministic_verification",
                    "verification",
                    "deterministic_verification",
                    ("independent_review", "final_synthesis"),
                ),
                AgentWorkflowNodeDefinition(
                    "independent_review",
                    "agent_model_execution",
                    "independent_review",
                    ("final_synthesis",),
                ),
                AgentWorkflowNodeDefinition(
                    "final_synthesis",
                    "model",
                    "final_synthesis",
                    ("workflow_completion",),
                ),
                AgentWorkflowNodeDefinition(
                    "workflow_completion",
                    "aggregation",
                    "workflow_completion",
                    (),
                ),
            ),
        )
    )
    return registry


# Construct the built-in definition during module import so invalid node names,
# output types, or connections stop application startup before requests arrive.
DEFAULT_WORKFLOW_DEFINITION_REGISTRY = create_default_workflow_definition_registry()
