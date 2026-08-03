"""ASGI integration tests for the provider-neutral Responses facade."""

import json

import httpx
from nexuspilot_models.contracts import ProviderName
from nexuspilot_models.providers.deepseek import DeepSeekChatProvider
from nexuspilot_models.registry import ProviderRegistry
from nexuspilot_models.transport import HttpTransport
from test_api import create_test_run

from nexuspilot_api.core.config import Settings
from nexuspilot_api.core.dependencies import get_provider_registry
from nexuspilot_api.infrastructure.provider_registry import create_provider_registry
from nexuspilot_api.main import app
from nexuspilot_api.schemas.responses import ResponsesRequest


def create_deepseek_stream_registry(
    *,
    include_done_marker: bool,
) -> tuple[ProviderRegistry, httpx.AsyncClient]:
    """Build a DeepSeek registry backed by deterministic Chat Completions SSE data."""

    stream_parts = [
        "data: "
        + json.dumps(
            {
                "id": "deepseek-request-1",
                "choices": [
                    {
                        "delta": {
                            "reasoning_content": "private reasoning must not be public",
                            "content": "public answer",
                        },
                        "finish_reason": "stop",
                    }
                ],
            }
        ),
        "data: "
        + json.dumps(
            {
                "choices": [],
                "usage": {"prompt_tokens": 12, "completion_tokens": 4},
            }
        ),
    ]
    if include_done_marker:
        stream_parts.append("data: [DONE]")
    stream_body = "\n\n".join(stream_parts) + "\n\n"

    def handle_request(request: httpx.Request) -> httpx.Response:
        """Return one fixed DeepSeek SSE response and assert the selected endpoint."""

        assert request.url.path == "/chat/completions"
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            text=stream_body,
        )

    provider_http_client = httpx.AsyncClient(transport=httpx.MockTransport(handle_request))
    registry = ProviderRegistry()
    registry.register(
        ProviderName.DEEPSEEK,
        DeepSeekChatProvider(
            transport=HttpTransport(provider_http_client, max_retries=0),
            base_url="https://api.deepseek.com",
            api_key="test-deepseek-key",
        ),
        allowed_models=frozenset({"deepseek-v4-flash"}),
    )
    return registry, provider_http_client


async def test_registered_provider_endpoint_reflects_dependency_registry(
    client: httpx.AsyncClient,
) -> None:
    """Verify callers can discover only adapters configured in the active application."""

    response = await client.get("/api/v1/providers")

    assert response.status_code == 200
    assert response.json() == {"providers": ["openai"]}


async def test_registry_uses_dedicated_deepseek_chat_provider() -> None:
    """Verify configured DeepSeek traffic resolves to its Chat Completions policy adapter."""

    http_client = httpx.AsyncClient()
    registry = create_provider_registry(
        Settings(
            _env_file=None,
            deepseek_api_key="test-deepseek-key",
            deepseek_models="deepseek-v4-flash,deepseek-v4-pro",
        ),
        http_client,
    )

    provider = registry.resolve(ProviderName.DEEPSEEK, "deepseek-v4-flash")
    await http_client.aclose()

    assert isinstance(provider, DeepSeekChatProvider)


def test_http_response_request_maps_reasoning_to_provider_contract() -> None:
    """Verify validated HTTP reasoning controls reach the provider-neutral request unchanged."""

    payload = ResponsesRequest.model_validate(
        {
            "run_id": "run-1",
            "provider": "deepseek",
            "model": "deepseek-v4-flash",
            "input": "Hello",
            "reasoning": {"enabled": True, "effort": "max"},
        }
    )

    model_request = payload.to_model_request()

    assert model_request.reasoning is not None
    assert model_request.reasoning.enabled is True
    assert model_request.reasoning.effort is not None
    assert model_request.reasoning.effort.value == "max"


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


async def test_deepseek_stream_hides_reasoning_and_completes_attempt(
    client: httpx.AsyncClient,
) -> None:
    """Verify DeepSeek reasoning stays in raw evidence and never enters public SSE."""

    run = await create_test_run(client)
    registry, provider_http_client = create_deepseek_stream_registry(
        include_done_marker=True
    )
    app.dependency_overrides[get_provider_registry] = lambda: registry
    try:
        response = await client.post(
            "/api/v1/responses",
            json={
                "run_id": run["run_id"],
                "provider": "deepseek",
                "model": "deepseek-v4-flash",
                "input": "Explain safely",
                "stream": True,
            },
        )
    finally:
        await provider_http_client.aclose()

    data_lines = [line[6:] for line in response.text.splitlines() if line.startswith("data: ")]
    events = [json.loads(line) for line in data_lines]
    run_detail = (await client.get(f"/api/v1/runs/{run['run_id']}")).json()

    assert response.status_code == 200
    assert [event["type"] for event in events] == [
        "response.started",
        "response.text.delta",
        "response.usage",
        "response.completed",
    ]
    assert "private reasoning must not be public" not in response.text
    assert events[-1]["data"]["response"]["output_text"] == "public answer"
    assert run_detail["attempts"][0]["status"] == "completed"


async def test_deepseek_stream_without_done_fails_attempt(
    client: httpx.AsyncClient,
) -> None:
    """Verify a truncated DeepSeek stream emits failure and persists a failed Attempt."""

    run = await create_test_run(client)
    registry, provider_http_client = create_deepseek_stream_registry(
        include_done_marker=False
    )
    app.dependency_overrides[get_provider_registry] = lambda: registry
    try:
        response = await client.post(
            "/api/v1/responses",
            json={
                "run_id": run["run_id"],
                "provider": "deepseek",
                "model": "deepseek-v4-flash",
                "input": "Explain safely",
                "stream": True,
            },
        )
    finally:
        await provider_http_client.aclose()

    data_lines = [line[6:] for line in response.text.splitlines() if line.startswith("data: ")]
    events = [json.loads(line) for line in data_lines]
    run_detail = (await client.get(f"/api/v1/runs/{run['run_id']}")).json()
    attempt = run_detail["attempts"][0]

    assert response.status_code == 200
    assert [event["type"] for event in events] == [
        "response.started",
        "response.text.delta",
        "response.usage",
        "response.failed",
    ]
    assert events[-1]["data"]["error"]["type"] == "response_parse_error"
    assert attempt["status"] == "failed"
    assert attempt["error_code"] == "response_parse_error"


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


async def test_response_rejects_unknown_and_deprecated_provider_fields(
    client: httpx.AsyncClient,
) -> None:
    """Verify unsupported provider parameters cannot be accepted and silently discarded."""

    run = await create_test_run(client)
    base_payload = {
        "run_id": run["run_id"],
        "provider": "deepseek",
        "model": "deepseek-v4-flash",
        "input": "Hello",
    }

    for field_name in ["frequency_penalty", "presence_penalty", "unknown_option"]:
        response = await client.post(
            "/api/v1/responses",
            json={**base_payload, field_name: 1},
        )
        assert response.status_code == 422
