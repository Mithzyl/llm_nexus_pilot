"""Regression tests for the backend MVC-style module boundaries."""

import subprocess
import sys
from pathlib import Path

import pytest

from nexuspilot_api.features.agent_runtime.routers import agent_workflows
from nexuspilot_api.routers import (
    context_builds,
    evaluations,
    internal_agent_working_memory,
    internal_audit,
    internal_collaboration_memory,
    knowledge,
    memories,
    model_attempts,
    projects,
    prompt_catalog,
    providers,
    responses,
    run_artifacts,
    runs,
    session_memory,
    sessions,
    tasks,
    user_memory,
    users,
)

PACKAGE_ROOT = Path(__file__).parents[1] / "src" / "nexuspilot_api"
AGENT_RUNTIME_FEATURE_ROOT = PACKAGE_ROOT / "features" / "agent_runtime"
MEMORY_FEATURE_ROOT = PACKAGE_ROOT / "features" / "memory"


@pytest.mark.parametrize(
    ("router", "expected_paths"),
    [
        (providers.router, {"/providers"}),
        (responses.router, {"/responses"}),
        (users.router, {"/users", "/users/{user_id}"}),
        (
            sessions.router,
            {
                "/sessions",
                "/sessions/{session_id}",
                "/sessions/{session_id}/messages",
                "/messages/{message_id}",
            },
        ),
        (runs.router, {"/runs", "/runs/{run_id}", "/runs/{run_id}/cancel"}),
        (
            tasks.router,
            {
                "/runs/{run_id}/tasks",
                "/tasks",
                "/tasks/{task_id}",
                "/tasks/{task_id}/cancel",
                "/tasks/{task_id}/retry",
            },
        ),
        (
            memories.router,
            {
                "/memories",
                "/memories/{memory_id}",
                "/memories/{memory_id}/versions",
                "/memories/{memory_id}/mutations",
                "/memory-retrievals",
                "/memory-retrievals/{memory_retrieval_id}",
            },
        ),
        (
            model_attempts.router,
            {
                "/runs/{run_id}/attempts",
                "/attempts",
                "/attempts/{attempt_id}",
                "/attempts/{attempt_id}/retries",
                "/attempt-retries/{retry_id}",
            },
        ),
        (
            run_artifacts.router,
            {
                "/runs/{run_id}/artifacts",
                "/artifacts",
                "/artifacts/{artifact_id}",
                "/artifacts/{artifact_id}/content",
            },
        ),
        (
            internal_audit.router,
            {
                "/internal/tool-calls",
                "/internal/tool-calls/{tool_call_id}",
                "/internal/evaluations",
                "/internal/evaluations/{evaluation_id}",
                "/internal/outbox-events",
                "/internal/outbox-events/{event_id}",
            },
        ),
        (
            session_memory.router,
            {
                "/sessions/{session_id}/memory",
                "/sessions/{session_id}/summaries",
            },
        ),
        (
            internal_agent_working_memory.router,
            {
                "/internal/agent-runs",
                "/internal/agent-runs/{agent_run_id}/working-memory",
                "/internal/agent-runs/{agent_run_id}/working-memory/checkpoints",
                "/internal/agent-runs/{agent_run_id}/working-memory/versions",
                "/internal/agent-runs/{agent_run_id}/status",
            },
        ),
        (
            internal_collaboration_memory.router,
            {
                "/internal/runs/{run_id}/agent-handoffs",
                "/internal/runs/{run_id}/memory",
                "/internal/runs/{run_id}/memory/rebuild",
                "/internal/agent-runs/{agent_run_id}/memory-packet",
            },
        ),
        (
            projects.router,
            {
                "/projects",
                "/projects/{project_id}",
                "/projects/{project_id}/memory",
                "/projects/{project_id}/memory-policy",
                "/projects/{project_id}/memory-candidates",
                "/projects/{project_id}/memory-candidates/{memory_id}/approve",
                "/projects/{project_id}/memory/rebuild",
                "/projects/{project_id}/workspaces",
            },
        ),
        (
            user_memory.router,
            {
                "/users/{user_id}/memory-profile",
                "/users/{user_id}/memory-profile/rebuild",
                "/users/{user_id}/memory-candidates",
                "/users/{user_id}/memory-candidates/{memory_id}/approve",
                "/users/{user_id}/memory-candidates/{memory_id}/reject",
            },
        ),
        (
            context_builds.router,
            {
                "/context-builds/preview",
                "/context-builds/{context_build_id}",
            },
        ),
        (
            knowledge.router,
            {
                "/knowledge-documents",
                "/knowledge-documents/{document_id}",
                "/knowledge-documents/{document_id}/versions",
                "/knowledge-retrievals",
            },
        ),
        (
            prompt_catalog.router,
            {
                "/prompt-renders",
                "/model-capabilities",
                "/internal/prompt-templates",
                "/internal/prompt-templates/{template_name}/versions",
                "/internal/prompt-templates/{template_name}/active-version",
                "/internal/prompt-templates/{template_name}/status",
                "/internal/model-catalog-versions",
                "/internal/model-catalog-versions/{catalog_version_id}/status",
            },
        ),
        (
            evaluations.router,
            {
                "/evaluations",
                "/evaluations/{evaluation_id}",
                "/internal/evaluation-rule-sets",
                "/internal/evaluation-rule-sets/{rule_set_id}/status",
            },
        ),
        (
            agent_workflows.router,
            {
                "/runs/{run_id}/agent-workflows",
                "/runs/{run_id}/agent-workflow",
                "/agent-workflows/{workflow_execution_id}",
                "/agent-workflows/{workflow_execution_id}/result",
                "/agent-workflows/{workflow_execution_id}/nodes",
                "/agent-workflows/{workflow_execution_id}/nodes/{node_execution_id}",
                "/agent-workflows/{workflow_execution_id}/events",
            },
        ),
    ],
)
def test_each_resource_owns_its_routes(router: object, expected_paths: set[str]) -> None:
    """Ensure resource controllers remain split instead of returning to one route file."""

    actual_paths = {route.path for route in router.routes}
    assert actual_paths == expected_paths


def test_resource_routers_do_not_import_persistence_models() -> None:
    """Ensure HTTP controllers delegate transactions instead of importing ORM models."""

    router_directories = [
        PACKAGE_ROOT / "routers",
        AGENT_RUNTIME_FEATURE_ROOT / "routers",
        MEMORY_FEATURE_ROOT / "routers",
    ]
    for router_directory in router_directories:
        for path in router_directory.glob("*.py"):
            if path.name in {"__init__.py", "common.py"}:
                continue
            source = path.read_text()
            assert "nexuspilot_api.models" not in source
            assert "from sqlalchemy" not in source


def test_agent_workflow_orm_module_can_be_imported_directly() -> None:
    """Verify feature-model imports do not depend on a lucky application import order."""

    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from nexuspilot_api.features.agent_runtime.models.agent_workflow "
                "import LlmAgentWorkflowExecution"
            ),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr


def test_memory_feature_keeps_mvc_layers_together() -> None:
    """Ensure the large Memory domain remains grouped without flattening global layers."""

    assert {
        child.name
        for child in MEMORY_FEATURE_ROOT.iterdir()
        if child.is_dir() and not child.name.startswith("__")
    } == {"models", "routers", "schemas", "services"}
    for global_layer in ("models", "routers", "schemas", "services"):
        assert not any((PACKAGE_ROOT / global_layer).glob("*memory*.py"))
