"""API tests for validation, ownership, storage, and provider failure paths."""

from types import SimpleNamespace

import httpx
from nexuspilot_models.contracts import (
    ModelRequest,
    ProviderName,
    TransportAttempt,
)
from nexuspilot_models.errors import ModelProviderError
from nexuspilot_models.registry import ProviderRegistry
from test_api import create_test_run

from nexuspilot_api.core.dependencies import get_provider_registry
from nexuspilot_api.infrastructure.object_storage import (
    ObjectStorageError,
    StoredObject,
    get_object_storage,
)
from nexuspilot_api.main import app
from nexuspilot_api.routers.run_artifacts import sanitize_run_artifact_filename


class FailingObjectStorage:
    """Simulate an unavailable object store without writing external state."""

    async def put_bytes(
        self,
        object_name: str,
        content: bytes,
        content_type: str,
    ) -> StoredObject:
        """Raise a deterministic storage failure for rollback tests."""

        del object_name, content, content_type
        raise ObjectStorageError("object store unavailable")


class TimeoutProvider:
    """Simulate a provider timeout carrying two physical request attempts."""

    name = ProviderName.OPENAI

    async def generate(self, request: ModelRequest) -> None:
        """Raise a timeout with retry evidence instead of returning a response."""

        del request
        raise ModelProviderError(
            "timeout",
            "Model provider request timed out.",
            retryable=True,
            raw_error={"error": {"type": "timeout"}},
            transport_attempts=[
                TransportAttempt(
                    attempt_index=1,
                    latency_ms=10,
                    error_type="timeout",
                    error_message="ReadTimeout",
                ),
                TransportAttempt(
                    attempt_index=2,
                    latency_ms=12,
                    error_type="timeout",
                    error_message="ReadTimeout",
                ),
            ],
        )

    async def stream(self, request: ModelRequest):
        """Expose the protocol method while producing no stream events."""

        del request
        if False:
            yield


def timeout_provider_registry() -> ProviderRegistry:
    """Return a registry whose OpenAI adapter always times out."""

    registry = ProviderRegistry()
    registry.register(
        ProviderName.OPENAI,
        TimeoutProvider(),
        allowed_models=frozenset({"test-model"}),
    )
    return registry


async def test_duplicate_user_returns_conflict(client: httpx.AsyncClient) -> None:
    """Verify the user controller exposes duplicate identity conflicts."""

    payload = {"user_id": "duplicate-user", "display_name": "First"}
    assert (await client.post("/api/v1/users", json=payload)).status_code == 201
    response = await client.post("/api/v1/users", json=payload)

    assert response.status_code == 409
    assert response.json()["detail"] == "User already exists"


async def test_missing_run_and_task_return_not_found(client: httpx.AsyncClient) -> None:
    """Verify lookup controllers consistently expose missing resources as HTTP 404."""

    run_response = await client.get("/api/v1/runs/missing-run")
    task_response = await client.get("/api/v1/tasks/missing-task")

    assert run_response.status_code == 404
    assert task_response.status_code == 404


async def test_duplicate_task_dependencies_fail_validation(client: httpx.AsyncClient) -> None:
    """Verify duplicate task dependencies are rejected before database writes."""

    run = await create_test_run(client)
    response = await client.post(
        f"/api/v1/runs/{run['run_id']}/tasks",
        json={
            "task_type": "planning",
            "title": "Invalid dependencies",
            "objective": "Must not be stored",
            "depends_on_task_ids": ["same-task", "same-task"],
        },
    )

    assert response.status_code == 422
    detail = (await client.get(f"/api/v1/runs/{run['run_id']}")).json()
    assert detail["tasks"] == []


async def test_attempt_task_must_belong_to_run(client: httpx.AsyncClient) -> None:
    """Verify attempt records cannot reference a task owned by another run."""

    first_run = await create_test_run(client)
    second_run = await create_test_run(client)
    task = (
        await client.post(
            f"/api/v1/runs/{first_run['run_id']}/tasks",
            json={"task_type": "test", "title": "Task", "objective": "Test"},
        )
    ).json()
    response = await client.post(
        f"/api/v1/runs/{second_run['run_id']}/attempts",
        json={
            "task_id": task["task_id"],
            "provider": "openai",
            "model": "test-model",
            "status": "completed",
        },
    )

    assert response.status_code == 422
    detail = (await client.get(f"/api/v1/runs/{second_run['run_id']}")).json()
    assert detail["attempts"] == []


def test_upload_filename_sanitizes_posix_and_windows_paths() -> None:
    """Verify browser-supplied path fragments cannot become object path segments."""

    assert sanitize_run_artifact_filename("../../secret.txt") == "secret.txt"
    assert sanitize_run_artifact_filename(r"C:\private\secret.txt") == "secret.txt"
    assert sanitize_run_artifact_filename(None) == "artifact.bin"


async def test_artifact_size_limit_rejects_before_storage(
    client: httpx.AsyncClient,
    monkeypatch,
) -> None:
    """Verify oversized uploads are rejected without creating artifact metadata."""

    from nexuspilot_api.routers import run_artifacts

    monkeypatch.setattr(
        run_artifacts,
        "get_settings",
        lambda: SimpleNamespace(max_artifact_size_bytes=4),
    )
    run = await create_test_run(client)
    response = await client.post(
        f"/api/v1/runs/{run['run_id']}/artifacts",
        data={"artifact_type": "report"},
        files={"file": ("report.txt", b"12345", "text/plain")},
    )

    assert response.status_code == 413
    detail = (await client.get(f"/api/v1/runs/{run['run_id']}")).json()
    assert detail["artifacts"] == []


async def test_storage_failure_rolls_back_artifact_metadata(client: httpx.AsyncClient) -> None:
    """Verify a failed object upload cannot leave pending artifact metadata committed."""

    app.dependency_overrides[get_object_storage] = FailingObjectStorage
    run = await create_test_run(client)
    response = await client.post(
        f"/api/v1/runs/{run['run_id']}/artifacts",
        data={"artifact_type": "report"},
        files={"file": ("report.txt", b"content", "text/plain")},
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "Object storage unavailable"
    detail = (await client.get(f"/api/v1/runs/{run['run_id']}")).json()
    assert detail["artifacts"] == []


async def test_provider_timeout_is_persisted_with_retry_evidence(
    client: httpx.AsyncClient,
) -> None:
    """Verify a timeout produces HTTP 504 and durable logical and physical attempt facts."""

    app.dependency_overrides[get_provider_registry] = timeout_provider_registry
    run = await create_test_run(client)
    response = await client.post(
        "/api/v1/responses",
        json={
            "run_id": run["run_id"],
            "provider": "openai",
            "model": "test-model",
            "input": "Hello",
        },
    )

    assert response.status_code == 504
    assert response.json()["error"]["type"] == "timeout"
    detail = (await client.get(f"/api/v1/runs/{run['run_id']}")).json()
    attempt = detail["attempts"][0]
    assert attempt["status"] == "timed_out"
    assert attempt["retry_count"] == 1
    assert [retry["attempt_index"] for retry in attempt["retries"]] == [1, 2]
    assert attempt["raw_response_uri"].endswith("/raw-error.json")
