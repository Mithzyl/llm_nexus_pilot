"""ASGI integration tests for the provider-neutral Responses facade."""

import json

import httpx
from test_api import create_test_run


async def test_registered_provider_endpoint_reflects_dependency_registry(
    client: httpx.AsyncClient,
) -> None:
    """Verify callers can discover only adapters configured in the active application."""

    response = await client.get("/api/v1/providers")

    assert response.status_code == 200
    assert response.json() == {"providers": ["openai"]}


async def test_non_streaming_response_persists_attempt_and_cost(
    client: httpx.AsyncClient,
) -> None:
    """Verify the facade routes a model and exposes durable normalized accounting."""

    run = await create_test_run(client)
    response = await client.post(
        "/api/v1/responses",
        json={
            "run_id": run["run_id"],
            "provider": "openai",
            "model": "test-model",
            "input": "Hello",
            "idempotency_key": "request-key-0001",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["object"] == "response"
    assert body["output_text"] == "answer for test-model"
    assert body["usage"]["estimated_cost"] == "0.001520"

    detail = (await client.get(f"/api/v1/runs/{run['run_id']}")).json()
    attempt = detail["attempts"][0]
    assert attempt["status"] == "completed"
    assert attempt["request_key"] == "request-key-0001"
    assert attempt["retry_count"] == 0
    assert len(attempt["retries"]) == 1
    assert attempt["retries"][0]["status_code"] == 200


async def test_response_rejects_duplicate_idempotency_key(client: httpx.AsyncClient) -> None:
    """Verify a repeated logical request key cannot trigger duplicate provider billing."""

    run = await create_test_run(client)
    payload = {
        "run_id": run["run_id"],
        "provider": "openai",
        "model": "test-model",
        "input": "Hello",
        "idempotency_key": "request-key-duplicate",
    }
    assert (await client.post("/api/v1/responses", json=payload)).status_code == 200
    assert (await client.post("/api/v1/responses", json=payload)).status_code == 409


async def test_streaming_response_uses_stable_sse_events(client: httpx.AsyncClient) -> None:
    """Verify stream events carry attempt IDs and end with the public response envelope."""

    run = await create_test_run(client)
    response = await client.post(
        "/api/v1/responses",
        json={
            "run_id": run["run_id"],
            "provider": "openai",
            "model": "test-model",
            "input": "Hello",
            "stream": True,
        },
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    data_lines = [line[6:] for line in response.text.splitlines() if line.startswith("data: ")]
    events = [json.loads(line) for line in data_lines]
    assert [event["type"] for event in events] == [
        "response.started",
        "response.text.delta",
        "response.completed",
    ]
    assert all(event["data"]["attempt_id"] for event in events)
    assert events[-1]["data"]["response"]["output_text"] == "streamed answer"


async def test_unconfigured_provider_returns_stable_error(client: httpx.AsyncClient) -> None:
    """Verify absent credentials produce a clear platform error instead of an attribute failure."""

    run = await create_test_run(client)
    response = await client.post(
        "/api/v1/responses",
        json={
            "run_id": run["run_id"],
            "provider": "anthropic",
            "model": "any-model",
            "input": "Hello",
        },
    )

    assert response.status_code == 503
    assert response.json()["error"]["type"] == "provider_not_configured"
