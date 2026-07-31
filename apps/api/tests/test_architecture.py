"""Regression tests for the backend MVC-style module boundaries."""

from pathlib import Path

import pytest

from nexuspilot_api.routers import (
    internal_audit,
    model_attempts,
    providers,
    responses,
    run_artifacts,
    runs,
    sessions,
    tasks,
    users,
)

PACKAGE_ROOT = Path(__file__).parents[1] / "src" / "nexuspilot_api"


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
    ],
)
def test_each_resource_owns_its_routes(router: object, expected_paths: set[str]) -> None:
    """Ensure resource controllers remain split instead of returning to one route file."""

    actual_paths = {route.path for route in router.routes}
    assert actual_paths == expected_paths


def test_resource_routers_do_not_import_persistence_models() -> None:
    """Ensure HTTP controllers delegate transactions instead of importing ORM models."""

    for path in (PACKAGE_ROOT / "routers").glob("*.py"):
        if path.name in {"__init__.py", "common.py"}:
            continue
        source = path.read_text()
        assert "nexuspilot_api.models" not in source
        assert "from sqlalchemy" not in source
