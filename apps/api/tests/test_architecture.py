"""Regression tests for the backend MVC-style module boundaries."""

from pathlib import Path

import pytest

from nexuspilot_api.routers import artifacts, attempts, providers, responses, runs, tasks, users

PACKAGE_ROOT = Path(__file__).parents[1] / "src" / "nexuspilot_api"


@pytest.mark.parametrize(
    ("router", "expected_paths"),
    [
        (providers.router, {"/providers"}),
        (responses.router, {"/responses"}),
        (users.router, {"/users"}),
        (runs.router, {"/runs", "/runs/{run_id}"}),
        (tasks.router, {"/runs/{run_id}/tasks", "/tasks/{task_id}"}),
        (attempts.router, {"/runs/{run_id}/attempts"}),
        (artifacts.router, {"/runs/{run_id}/artifacts"}),
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
