"""Behavior tests for the phase-one NexusPilot API contract."""

from decimal import Decimal

import httpx


async def create_test_run(client: httpx.AsyncClient) -> dict:
    """Create and return a reusable run fixture through the public HTTP contract."""

    user_id = f"user-{create_test_run.counter}"
    create_test_run.counter += 1
    user_response = await client.post(
        "/api/v1/users",
        json={"user_id": user_id, "display_name": "Test User"},
    )
    assert user_response.status_code == 201
    response = await client.post(
        "/api/v1/runs",
        json={
            "user_id": user_id,
            "session_id": "session-1",
            "user_request": "Investigate a repository",
            "budget_limit": "5.000000",
        },
    )
    assert response.status_code == 201
    return response.json()


create_test_run.counter = 1


async def test_health_does_not_require_authentication(client: httpx.AsyncClient) -> None:
    """Verify infrastructure probes can access liveness without credentials."""

    response = await client.get("/health", headers={"X-API-Key": ""})
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


async def test_protected_endpoint_rejects_missing_key(client: httpx.AsyncClient) -> None:
    """Verify business endpoints reject callers that omit the configured API key."""

    response = await client.get("/api/v1/runs/missing", headers={"X-API-Key": ""})
    assert response.status_code == 401


async def test_run_task_attempt_and_detail_flow(client: httpx.AsyncClient) -> None:
    """Verify core records remain linked and attempt costs roll up to their run."""

    run = await create_test_run(client)
    first_task_response = await client.post(
        f"/api/v1/runs/{run['run_id']}/tasks",
        json={
            "task_type": "repository_research",
            "title": "Inspect entrypoints",
            "objective": "Find API entrypoints with evidence",
            "assigned_role": "researcher",
        },
    )
    assert first_task_response.status_code == 201
    first_task = first_task_response.json()
    dependent_response = await client.post(
        f"/api/v1/runs/{run['run_id']}/tasks",
        json={
            "task_type": "planning",
            "title": "Create plan",
            "objective": "Plan changes from research",
            "depends_on_task_ids": [first_task["task_id"]],
        },
    )
    assert dependent_response.status_code == 201
    assert dependent_response.json()["status"] == "waiting_for_dependency"

    attempt_response = await client.post(
        f"/api/v1/runs/{run['run_id']}/attempts",
        json={
            "task_id": first_task["task_id"],
            "provider": "openai",
            "model": "test-model",
            "status": "completed",
            "input_tokens": 100,
            "output_tokens": 20,
            "estimated_cost": "0.012500",
            "latency_ms": 500,
        },
    )
    assert attempt_response.status_code == 201

    detail_response = await client.get(f"/api/v1/runs/{run['run_id']}")
    assert detail_response.status_code == 200
    detail = detail_response.json()
    assert len(detail["tasks"]) == 2
    assert len(detail["attempts"]) == 1
    assert Decimal(detail["cost_used"]) == Decimal("0.012500")


async def test_task_dependency_must_belong_to_same_run(client: httpx.AsyncClient) -> None:
    """Verify cross-run dependency edges cannot corrupt a run's task graph."""

    first_run = await create_test_run(client)
    second_run = await create_test_run(client)
    task_response = await client.post(
        f"/api/v1/runs/{first_run['run_id']}/tasks",
        json={"task_type": "research", "title": "Research", "objective": "Inspect files"},
    )
    foreign_task_id = task_response.json()["task_id"]
    response = await client.post(
        f"/api/v1/runs/{second_run['run_id']}/tasks",
        json={
            "task_type": "plan",
            "title": "Plan",
            "objective": "Make a plan",
            "depends_on_task_ids": [foreign_task_id],
        },
    )
    assert response.status_code == 422


async def test_artifact_upload_saves_metadata(client: httpx.AsyncClient) -> None:
    """Verify artifact bytes go to object storage while metadata appears in run history."""

    run = await create_test_run(client)
    response = await client.post(
        f"/api/v1/runs/{run['run_id']}/artifacts",
        data={"artifact_type": "report"},
        files={"file": ("result.txt", b"verified result", "text/plain")},
    )
    assert response.status_code == 201
    artifact = response.json()
    assert artifact["filename"] == "result.txt"
    assert artifact["size_bytes"] == 15
    assert artifact["storage_uri"].startswith("memory://")
