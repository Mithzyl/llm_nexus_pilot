"""Architecture dependency tests for the Phase 5 Agent Runtime services."""

from pathlib import Path

AGENT_RUNTIME_FEATURE_ROOT = (
    Path(__file__).parents[1] / "src" / "nexuspilot_api" / "features" / "agent_runtime"
)


def test_agent_runtime_state_service_does_not_depend_on_provider_or_orchestrator() -> None:
    """Keep durable state transitions independent from Provider and orchestration code."""

    state_service_source = (
        AGENT_RUNTIME_FEATURE_ROOT / "services" / "workflow_execution_state_service.py"
    ).read_text()

    assert "nexuspilot_models" not in state_service_source
    assert "ModelInvocationService" not in state_service_source
    assert "workflow_execution_service" not in state_service_source
    assert "FastAPI" not in state_service_source


def test_agent_model_node_service_uses_state_port_without_importing_execution_service() -> None:
    """Keep Provider invocation behind a state port instead of a circular service dependency."""

    model_node_service_source = (
        AGENT_RUNTIME_FEATURE_ROOT / "services" / "agent_model_node_service.py"
    ).read_text()

    assert "AgentModelNodeStatePort" in model_node_service_source
    assert "workflow_execution_service" not in model_node_service_source
    assert "FastAPI" not in model_node_service_source
