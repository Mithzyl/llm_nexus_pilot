"""ASGI integration tests for the provider-neutral Responses facade."""

import asyncio
import json
from decimal import Decimal

import httpx
import pytest
from nexuspilot_models.contracts import (
    ModelReasoningCapabilities,
    ModelRequest,
    ModelResponse,
    ProviderName,
    ReasoningContinuationMode,
    ReasoningPresentationCapability,
    StreamEvent,
    StreamEventType,
    TransportAttempt,
)
from nexuspilot_models.errors import ModelProviderError
from nexuspilot_models.pricing import ModelPrice, PriceCatalog
from nexuspilot_models.providers.anthropic import AnthropicMessagesProvider
from nexuspilot_models.providers.common import preserve_stream_usage
from nexuspilot_models.providers.deepseek import DeepSeekChatProvider
from nexuspilot_models.providers.openai import OpenAIResponsesProvider
from nexuspilot_models.registry import ProviderRegistry
from nexuspilot_models.transport import HttpTransport
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from test_api import create_test_run

from nexuspilot_api.core.config import Settings
from nexuspilot_api.core.dependencies import get_price_catalog, get_provider_registry
from nexuspilot_api.infrastructure.object_storage import (
    ObjectStorageError,
    StoredObject,
    get_object_storage,
)
from nexuspilot_api.infrastructure.provider_registry import create_provider_registry
from nexuspilot_api.main import app
from nexuspilot_api.models import AttemptStatus, LlmModelAttempt
from nexuspilot_api.schemas.responses import ResponsesRequest
from nexuspilot_api.services.model_response_service import (
    ModelInvocationService,
    calculate_prompt_prefix_fingerprint,
)


@pytest.mark.parametrize("ending", ["cancel", "deadline"])
@pytest.mark.parametrize("storage_failure", [False, True])
async def test_collected_stream_interruption_persists_draft_without_completing(
    client, ending, storage_failure, monkeypatch,
):
    """An interrupted collected Agent response retains a private draft and known usage."""
    waiting = asyncio.Event()

    class CollectedStreamProvider:
        """Simulate the same stream-to-generate collection used by Strata, without a network."""

        @preserve_stream_usage
        async def stream(self, request):
            """Send draft and partial accounting, then wait for cancellation or deadline."""
            yield StreamEvent(type=StreamEventType.TEXT_DELTA, sequence=1, data={"delta": "草稿"})
            yield StreamEvent(type=StreamEventType.USAGE, sequence=2, data={"input_tokens": 25})
            waiting.set()
            await asyncio.Event().wait()

        async def generate(self, request):
            """Return only completed responses, so interruption must never return a draft."""
            async for event in self.stream(request):
                if event.type == StreamEventType.COMPLETED:
                    return ModelResponse.model_validate(event.data["response"])
            raise AssertionError("test stream must be interrupted")

    registry = ProviderRegistry()
    registry.register(
        ProviderName.OPENAI, CollectedStreamProvider(), allowed_models=frozenset({"test-model"}),
    )
    app.dependency_overrides[get_provider_registry] = lambda: registry
    storage = app.dependency_overrides[get_object_storage]()
    if storage_failure:
        original_put = storage.put_bytes

        async def fail_interrupted_write(name, content, content_type):
            """Fail only terminal audit writes, preserving requests and unrelated fixture data."""
            if name.endswith(("/interrupted-response.json", "/raw-error.json")):
                raise ObjectStorageError("synthetic unavailable storage")
            return await original_put(name, content, content_type)

        monkeypatch.setattr(storage, "put_bytes", fail_interrupted_write)
    run = await create_test_run(client)
    task = asyncio.create_task(client.post("/api/v1/responses", json={
        "run_id": run["run_id"], "provider": "openai", "model": "test-model",
        "input": "test interruption", "timeout_seconds": 1,
    }))
    await asyncio.wait_for(waiting.wait(), timeout=2)
    if ending == "cancel":
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        response = await task
        assert response.status_code == 504
    detail = (await client.get(f"/api/v1/runs/{run['run_id']}")).json()
    attempt = detail["attempts"][0]
    assert attempt["status"] == ("outcome_unknown" if ending == "cancel" else "timed_out")
    if storage_failure:
        assert attempt["raw_response_uri"] is None
    else:
        import hashlib

        assert attempt["raw_response_uri"] is not None
        raw = json.loads(storage.objects[attempt["raw_response_uri"]][0])
        progress = raw["stream_progress"]
        assert progress["response_complete"] is False
        assert progress["text"] == "草稿"
        assert progress["text_sha256"] == hashlib.sha256("草稿".encode()).hexdigest()
        assert progress["text_characters"] == 2
    assert attempt["input_tokens"] == 25
    assert attempt["output_tokens"] is None
    assert attempt["estimated_cost"] is None
    assert attempt["first_visible_token_at"] is not None
    assert len(detail["attempts"]) == 1


async def test_platform_stream_deadline_preserves_observed_usage(
    client, test_database_session_factory,
):
    """Keep observed counters on deadline expiry while the Run cancellation observer is active."""
    from types import SimpleNamespace

    from test_api import create_test_run

    run = await create_test_run(client)

    class Stalled:
        """Provide accounting progress before waiting indefinitely."""

        async def stream(self, request):
            """Report a known input count, without inventing an output count."""
            yield StreamEvent(type=StreamEventType.USAGE, sequence=1, data={"input_tokens": 25})
            await asyncio.Event().wait()

    request = ModelRequest(
        provider=ProviderName.OPENAI, model="test", messages=[{"role": "user", "content": "test"}]
    )
    async with test_database_session_factory() as db:
        with pytest.raises(ModelProviderError) as caught:
            _ = [
                event
                async for event in ModelInvocationService._stream_provider_with_deadline(
                    SimpleNamespace(db_session=db),
                    Stalled(),
                    request,
                    timeout_seconds=0.01,
                    run_id=run["run_id"],
                )
            ]
    assert caught.value.error_type == "timeout"
    assert caught.value.response.input_tokens == 25
    assert caught.value.response.output_tokens is None
    assert caught.value.response.raw_response["usage_complete"] is False


class RawResponseFailingObjectStorage:
    """Persist raw requests but fail the post-provider raw-response audit write."""

    async def put_bytes(
        self,
        object_name: str,
        content: bytes,
        content_type: str,
    ) -> StoredObject:
        """Return request metadata and reject only raw-response object writes."""

        del content, content_type
        if object_name.endswith("/raw-response.json"):
            raise ObjectStorageError("raw response storage unavailable")
        return StoredObject(
            uri=f"memory://test/{object_name}",
            content_hash="request-content-hash",
            size_bytes=1,
        )


class BlockingRawResponseObjectStorage:
    """Pause a raw-response write so request cancellation can hit audit finalization."""

    def __init__(self) -> None:
        """Create signals for observing and releasing the response write."""

        self.response_write_started = asyncio.Event()
        self.allow_response_write = asyncio.Event()

    async def put_bytes(
        self,
        object_name: str,
        content: bytes,
        content_type: str,
    ) -> StoredObject:
        """Block only the response object and return stable in-memory metadata."""

        del content, content_type
        if object_name.endswith("/raw-response.json"):
            self.response_write_started.set()
            await self.allow_response_write.wait()
        return StoredObject(
            uri=f"memory://test/{object_name}",
            content_hash="stored-content-hash",
            size_bytes=1,
        )


class StallingStreamProvider:
    """Emit a start event and then exceed the platform stream deadline."""

    name = ProviderName.OPENAI

    async def stream(self, _request):
        """Yield one event before stalling so timeout finalization remains observable."""

        from nexuspilot_models.contracts import StreamEvent, StreamEventType

        yield StreamEvent(type=StreamEventType.STARTED, sequence=1)
        await asyncio.sleep(2)


class CapturingContextProvider:
    """Capture the exact provider-neutral request used by a context integration test."""

    name = ProviderName.OPENAI

    def __init__(self) -> None:
        """Initialize an empty ordered request list."""

        self.requests: list[ModelRequest] = []

    async def generate(self, request: ModelRequest) -> ModelResponse:
        """Record one request and return a stable completed response."""

        self.requests.append(request)
        return ModelResponse(
            text="context answer",
            finish_reason="stop",
            input_tokens=30,
            output_tokens=3,
            latency_ms=4,
            provider_request_id="context-provider-request",
            raw_response={"text": "context answer"},
            transport_attempts=[TransportAttempt(attempt_index=1, status_code=200, latency_ms=4)],
        )

    async def stream(self, _request: ModelRequest):
        """Reject streaming because this fixture verifies one non-streaming request."""

        raise AssertionError("CapturingContextProvider does not support streaming")
        yield


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
        reasoning_capabilities_by_model={
            "deepseek-v4-flash": ModelReasoningCapabilities(
                presentation=ReasoningPresentationCapability.RAW,
                supports_streaming_presentation=True,
                continuation=ReasoningContinuationMode.RAW_REASONING_REPLAY,
                supports_reasoning_tokens=True,
            )
        },
    )
    return registry, provider_http_client


async def test_registered_provider_endpoint_reflects_dependency_registry(
    client: httpx.AsyncClient,
) -> None:
    """Verify callers can discover configured adapters and their selectable models."""

    response = await client.get("/api/v1/providers")

    assert response.status_code == 200
    assert response.json() == {
        "providers": ["openai"],
        "models_by_provider": {"openai": ["test-model"]},
        "reasoning_capabilities_by_provider_model": {
            "openai": {
                "test-model": {
                    "presentation": "none",
                    "supports_streaming_presentation": False,
                    "continuation": "none",
                    "supports_reasoning_tokens": False,
                }
            }
        },
    }


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
        {ProviderName.DEEPSEEK: "https://gateway.example.com:8443/deepseek/v1"},
    )

    provider = registry.resolve(ProviderName.DEEPSEEK, "deepseek-v4-flash")
    await http_client.aclose()

    assert isinstance(provider, DeepSeekChatProvider)
    assert provider.base_url == "https://gateway.example.com:8443/deepseek/v1"


async def test_registry_does_not_register_provider_without_enabled_database_endpoint() -> None:
    """Keep an environment credential inactive until its Provider has an enabled DB URL."""

    http_client = httpx.AsyncClient()
    registry = create_provider_registry(
        Settings(
            _env_file=None,
            deepseek_api_key="test-deepseek-key",
            deepseek_models="deepseek-v4-flash",
        ),
        http_client,
        {},
    )
    await http_client.aclose()

    assert ProviderName.DEEPSEEK not in registry.registered_names


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


def test_http_response_request_accepts_the_provider_output_cap() -> None:
    """Verify the optional interface accepts the shared Provider output ceiling."""

    payload = ResponsesRequest.model_validate(
        {
            "run_id": "run-1",
            "provider": "deepseek",
            "model": "deepseek-v4-flash",
            "input": "Hello",
            "max_output_tokens": 65_536,
        }
    )

    assert payload.max_output_tokens == 65_536
    assert payload.to_model_request().max_output_tokens == 65_536


def test_prompt_prefix_fingerprint_ignores_dynamic_messages_and_mapping_order() -> None:
    """Verify equivalent stable contracts share a fingerprint across dynamic inputs."""

    first_request = ResponsesRequest(
        run_id="run-1",
        provider=ProviderName.OPENAI,
        model="test-model",
        input="first dynamic question",
        instructions="Use the stable response contract.",
        output_schema={"type": "object", "properties": {"answer": {"type": "string"}}},
    ).to_model_request()
    second_request = ResponsesRequest(
        run_id="run-2",
        provider=ProviderName.OPENAI,
        model="test-model",
        input="second dynamic question",
        instructions="Use the stable response contract.",
        output_schema={"properties": {"answer": {"type": "string"}}, "type": "object"},
    ).to_model_request()

    assert calculate_prompt_prefix_fingerprint(
        first_request
    ) == calculate_prompt_prefix_fingerprint(second_request)


async def test_response_uses_run_anchored_context_and_records_attempt_lineage(
    client: httpx.AsyncClient,
) -> None:
    """Verify quick responses use persisted Session history and expose its Context Build."""

    catalog = await client.post(
        "/api/v1/internal/model-catalog-versions",
        headers={"X-Internal-API-Key": "test-internal-key-long-enough"},
        json={
            "provider": "openai",
            "model": "test-model",
            "catalog_version": "2026-08-26",
            "source": "response-context-test",
            "context_window": 8_192,
            "supports_streaming": True,
            "tokenizer_name": "utf8_bytes_upper_bound",
            "tokenizer_version": "v1",
        },
    )
    assert catalog.status_code == 200
    assert (
        await client.post(
            "/api/v1/users",
            json={"user_id": "response-context-owner", "display_name": "Owner"},
        )
    ).status_code == 201
    conversation = (
        await client.post(
            "/api/v1/sessions",
            json={"user_id": "response-context-owner", "title": "Context"},
        )
    ).json()
    first_turn = (
        await client.post(
            f"/api/v1/sessions/{conversation['session_id']}/turns",
            json={"user_id": "response-context-owner", "content_text": "Remember beta"},
        )
    ).json()
    assert (
        await client.post(
            f"/api/v1/sessions/{conversation['session_id']}/messages",
            json={
                "role": "assistant",
                "content_text": "Beta is remembered",
                "run_id": first_turn["run"]["run_id"],
            },
        )
    ).status_code == 201
    current_turn = (
        await client.post(
            f"/api/v1/sessions/{conversation['session_id']}/turns",
            json={"user_id": "response-context-owner", "content_text": "What is remembered?"},
        )
    ).json()
    prompt = await client.post(
        "/api/v1/internal/prompt-templates",
        headers={"X-Internal-API-Key": "test-internal-key-long-enough"},
        json={
            "template_name": "response-product-prompt",
            "content_text": "Answer for {{ audience }}.",
            "variable_schema": {"audience": "string"},
            "created_by_actor_id": "response-context-test",
        },
    )
    assert prompt.status_code == 200
    artifact = await client.post(
        f"/api/v1/runs/{current_turn['run']['run_id']}/artifacts",
        data={"artifact_type": "context-test-fixture"},
        files={"file": ("brief.txt", b"Fixture-only artifact context", "text/plain")},
    )
    assert artifact.status_code == 201

    provider = CapturingContextProvider()
    registry = ProviderRegistry()
    registry.register(
        ProviderName.OPENAI,
        provider,
        allowed_models=frozenset({"test-model"}),
    )
    app.dependency_overrides[get_provider_registry] = lambda: registry
    response = await client.post(
        "/api/v1/responses",
        json={
            "run_id": current_turn["run"]["run_id"],
            "current_user_message_id": current_turn["message"]["message_id"],
            "provider": "openai",
            "model": "test-model",
            "input": "What is remembered?",
            "context_source_references": {
                "prompt": {
                    "template_name": "response-product-prompt",
                    "version_number": 1,
                    "variables": {"audience": "engineers"},
                },
                "artifact_ids": [artifact.json()["artifact_id"]],
            },
            "idempotency_key": "response-context-request-0001",
        },
    )

    assert response.status_code == 200
    assert provider.requests[0].system_instruction is not None
    assert "Answer for engineers." in provider.requests[0].system_instruction
    provider_message_texts = [message.content for message in provider.requests[0].messages]
    assert provider_message_texts[:3] == [
        "Remember beta",
        "Beta is remembered",
        "What is remembered?",
    ]
    assert "Fixture-only artifact context" in provider_message_texts[3]
    run_detail = (await client.get(f"/api/v1/runs/{current_turn['run']['run_id']}")).json()
    context_build_id = run_detail["attempts"][0]["context_build_id"]
    assert context_build_id is not None
    context_build = (await client.get(f"/api/v1/context-builds/{context_build_id}")).json()
    assert [
        message["content"] for message in context_build["messages"] if message["role"] != "system"
    ][:3] == ["Remember beta", "Beta is remembered", "What is remembered?"]

    evidence = await client.get(
        f"/api/v1/context-builds/{context_build_id}/evidence",
        params={"user_id": "response-context-owner"},
    )
    cross_user_evidence = await client.get(
        f"/api/v1/context-builds/{context_build_id}/evidence",
        params={"user_id": "another-owner"},
    )

    assert evidence.status_code == 200
    assert "messages" not in evidence.json()
    assert "content_text" not in evidence.text
    assert {source["source_type"] for source in evidence.json()["sources"]} >= {
        "message",
        "prompt_release",
        "artifact",
    }
    assert cross_user_evidence.status_code == 404


async def test_non_streaming_response_persists_attempt_and_cost(
    client: httpx.AsyncClient,
) -> None:
    """Verify the facade applies medium model policy and persists its audit evidence."""

    registered_model = await client.post(
        "/api/v1/internal/models",
        headers={"X-Internal-API-Key": "test-internal-key-long-enough"},
        json={
            "provider": "openai",
            "model_identifier": "test-model",
            "display_name": "Test Model",
            "reasoning_supported_efforts": ["minimal", "medium", "xhigh"],
            "reasoning_supports_disable": True,
            "reasoning_default_enabled": True,
            "reasoning_default_effort": "medium",
            "reasoning_effort_mapping": {
                "minimal": "minimal",
                "medium": "medium",
                "xhigh": "xhigh",
            },
            "reasoning_profile_version": "openai.test.reasoning.v1",
        },
    )
    assert registered_model.status_code == 200
    run = await create_test_run(client)
    response = await client.post(
        "/api/v1/responses",
        json={
            "run_id": run["run_id"],
            "provider": "openai",
            "model": "test-model",
            "input": "Hello",
            "instructions": "Use the stable response contract.",
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
    assert len(attempt["prompt_prefix_fingerprint"]) == 64
    assert attempt["retry_count"] == 0
    assert attempt["reasoning_enabled"] is True
    assert attempt["reasoning_effort"] == "medium"
    assert attempt["reasoning_source"] == "model_default"
    assert attempt["reasoning_profile_version"] == "openai.test.reasoning.v1"
    assert attempt["reasoning_provider_options_json"] == {"provider_value": "medium"}
    assert len(attempt["retries"]) == 1
    assert attempt["retries"][0]["status_code"] == 200


async def test_response_rejects_oversized_agent_role_metadata_before_provider_call(
    client: httpx.AsyncClient,
) -> None:
    """Verify public metadata cannot overflow the persisted Agent role diagnostic field."""

    run = await create_test_run(client)
    response = await client.post(
        "/api/v1/responses",
        json={
            "run_id": run["run_id"],
            "provider": "openai",
            "model": "test-model",
            "input": "Hello",
            "metadata": {"agent_role": "x" * 65},
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "agent_role metadata must contain 1 to 64 characters"
    assert (await client.get(f"/api/v1/runs/{run['run_id']}")).json()["attempts"] == []


async def test_raw_response_storage_failure_preserves_provider_accounting(
    client: httpx.AsyncClient,
) -> None:
    """Verify a known provider response keeps billing facts when raw audit storage fails."""

    app.dependency_overrides[get_object_storage] = RawResponseFailingObjectStorage
    run = await create_test_run(client)
    payload = {
        "run_id": run["run_id"],
        "provider": "openai",
        "model": "test-model",
        "input": "Hello",
        "idempotency_key": "request-raw-response-failure-0001",
    }

    response = await client.post("/api/v1/responses", json=payload)

    assert response.status_code == 502
    assert response.json()["error"]["type"] == "response_audit_failed"
    assert response.json()["error"]["retryable"] is False
    detail = (await client.get(f"/api/v1/runs/{run['run_id']}")).json()
    attempt = detail["attempts"][0]
    assert attempt["status"] == "failed"
    assert attempt["input_tokens"] == 100
    assert attempt["output_tokens"] == 20
    assert attempt["cached_tokens"] == 10
    assert attempt["estimated_cost"] == "0.001520"
    assert attempt["provider_request_id"] is None
    assert attempt["raw_response_uri"] is None
    assert attempt["error_code"] == "response_audit_failed"
    assert len(attempt["retries"]) == 1
    assert detail["cost_used"] == "0.001520"
    assert (await client.post("/api/v1/responses", json=payload)).status_code == 409
    replay_detail = (await client.get(f"/api/v1/runs/{run['run_id']}")).json()
    assert len(replay_detail["attempts"]) == 1
    assert replay_detail["cost_used"] == "0.001520"


async def test_cancellation_during_response_audit_still_finalizes_known_provider_response(
    client: httpx.AsyncClient,
) -> None:
    """Verify caller cancellation cannot strand a known response in started state."""

    storage = BlockingRawResponseObjectStorage()
    app.dependency_overrides[get_object_storage] = lambda: storage
    run = await create_test_run(client)
    request_task = asyncio.create_task(
        client.post(
            "/api/v1/responses",
            json={
                "run_id": run["run_id"],
                "provider": "openai",
                "model": "test-model",
                "input": "Hello",
                "idempotency_key": "request-cancel-during-audit-0001",
            },
        )
    )
    await asyncio.wait_for(storage.response_write_started.wait(), timeout=2)
    request_task.cancel()
    storage.allow_response_write.set()

    with pytest.raises(asyncio.CancelledError):
        await request_task

    detail = (await client.get(f"/api/v1/runs/{run['run_id']}")).json()
    assert len(detail["attempts"]) == 1
    attempt = detail["attempts"][0]
    assert attempt["status"] == "completed"
    assert attempt["input_tokens"] == 100
    assert attempt["provider_request_id"] is None
    assert attempt["raw_response_uri"].endswith("/raw-response.json")
    assert detail["cost_used"] == "0.001520"


async def test_transient_response_commit_failure_reconciles_without_duplicate_cost(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify a failed completion commit is reloaded and safely retried once."""

    original_commit = AsyncSession.commit
    completion_commit_failed = False

    async def fail_first_completion_commit(db_session: AsyncSession) -> None:
        """Raise before the first commit containing a completed model Attempt."""

        nonlocal completion_commit_failed
        contains_completed_attempt = any(
            isinstance(item, LlmModelAttempt) and item.status == AttemptStatus.COMPLETED
            for item in db_session.identity_map.values()
        )
        if contains_completed_attempt and not completion_commit_failed:
            completion_commit_failed = True
            raise RuntimeError("simulated transient commit failure")
        await original_commit(db_session)

    monkeypatch.setattr(AsyncSession, "commit", fail_first_completion_commit)
    run = await create_test_run(client)
    response = await client.post(
        "/api/v1/responses",
        json={
            "run_id": run["run_id"],
            "provider": "openai",
            "model": "test-model",
            "input": "Hello",
            "idempotency_key": "request-transient-commit-0001",
        },
    )

    assert completion_commit_failed is True
    assert response.status_code == 200
    detail = (await client.get(f"/api/v1/runs/{run['run_id']}")).json()
    assert len(detail["attempts"]) == 1
    assert detail["attempts"][0]["status"] == "completed"
    assert len(detail["attempts"][0]["retries"]) == 1
    assert detail["cost_used"] == "0.001520"


async def test_repeated_response_commit_failure_preserves_usage_as_outcome_unknown(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify exhausted completion retries retain accounting instead of staying started."""

    original_commit = AsyncSession.commit
    completion_commit_failure_count = 0

    async def fail_two_completion_commits(db_session: AsyncSession) -> None:
        """Fail both completed-state commits before allowing unknown-state recovery."""

        nonlocal completion_commit_failure_count
        contains_completed_attempt = any(
            isinstance(item, LlmModelAttempt) and item.status == AttemptStatus.COMPLETED
            for item in db_session.identity_map.values()
        )
        if contains_completed_attempt and completion_commit_failure_count < 2:
            completion_commit_failure_count += 1
            raise RuntimeError("simulated repeated commit failure")
        await original_commit(db_session)

    monkeypatch.setattr(AsyncSession, "commit", fail_two_completion_commits)
    run = await create_test_run(client)
    response = await client.post(
        "/api/v1/responses",
        json={
            "run_id": run["run_id"],
            "provider": "openai",
            "model": "test-model",
            "input": "Hello",
            "idempotency_key": "request-repeated-commit-0001",
        },
    )

    assert completion_commit_failure_count == 2
    assert response.status_code == 502
    assert response.json()["error"]["type"] == "response_persistence_error"
    detail = (await client.get(f"/api/v1/runs/{run['run_id']}")).json()
    assert len(detail["attempts"]) == 1
    attempt = detail["attempts"][0]
    assert attempt["status"] == "outcome_unknown"
    assert attempt["input_tokens"] == 100
    assert attempt["estimated_cost"] == "0.001520"
    assert attempt["provider_request_id"] is None
    assert len(attempt["retries"]) == 1
    assert detail["cost_used"] == "0.001520"


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


async def test_response_rejects_model_name_longer_than_attempt_storage_column(
    client: httpx.AsyncClient,
) -> None:
    """Verify HTTP validation rejects a model name that MySQL cannot persist."""

    run = await create_test_run(client)
    response = await client.post(
        "/api/v1/responses",
        json={
            "run_id": run["run_id"],
            "provider": "openai",
            "model": "m" * 129,
            "input": "Hello",
        },
    )

    assert response.status_code == 422
    detail = (await client.get(f"/api/v1/runs/{run['run_id']}")).json()
    assert detail["attempts"] == []


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


async def test_stream_raw_response_storage_failure_emits_failure_and_keeps_accounting(
    client: httpx.AsyncClient,
    test_database_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Verify SSE audit failure is terminal, billed, and releases its database connection."""

    app.dependency_overrides[get_object_storage] = RawResponseFailingObjectStorage
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

    data_lines = [line[6:] for line in response.text.splitlines() if line.startswith("data: ")]
    events = [json.loads(line) for line in data_lines]
    detail = (await client.get(f"/api/v1/runs/{run['run_id']}")).json()
    attempt = detail["attempts"][0]

    assert response.status_code == 200
    assert [event["type"] for event in events] == [
        "response.started",
        "response.text.delta",
        "response.failed",
    ]
    assert events[-1]["data"]["error"]["type"] == "response_audit_failed"
    assert attempt["status"] == "failed"
    assert attempt["input_tokens"] == 100
    assert attempt["estimated_cost"] == "0.001520"
    assert attempt["provider_request_id"] is None
    assert len(attempt["retries"]) == 1
    assert detail["cost_used"] == "0.001520"
    database_engine = test_database_session_factory.kw["bind"]
    assert database_engine.sync_engine.pool.checkedout() == 0


async def test_deepseek_stream_hides_reasoning_and_completes_attempt(
    client: httpx.AsyncClient,
) -> None:
    """Verify default policy exposes only status while keeping raw text private."""

    run = await create_test_run(client)
    registry, provider_http_client = create_deepseek_stream_registry(include_done_marker=True)
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
        "reasoning.started",
        "response.text.delta",
        "response.usage",
        "reasoning.completed",
        "response.completed",
    ]
    assert "private reasoning must not be public" not in response.text
    assert events[-1]["data"]["response"]["output_text"] == "public answer"
    assert events[-1]["data"]["response"]["reasoning_blocks"][0]["kind"] == "reasoning.status"
    assert run_detail["attempts"][0]["status"] == "completed"

    attempt_id = run_detail["attempts"][0]["attempt_id"]
    replay = (await client.get(f"/api/v1/attempts/{attempt_id}/events")).json()
    snapshots = (await client.get(f"/api/v1/attempts/{attempt_id}/reasoning-blocks")).json()
    assert [item["type"] for item in replay["items"]] == [event["type"] for event in events]
    assert snapshots[0]["kind"] == "reasoning.status"
    assert snapshots[0]["text"] is None


async def test_deepseek_provider_visible_stream_exposes_only_authorized_raw_reasoning(
    client: httpx.AsyncClient,
) -> None:
    """Verify explicit provider-visible policy exposes raw text through normalized events."""

    run = await create_test_run(client)
    registry, provider_http_client = create_deepseek_stream_registry(include_done_marker=True)
    app.dependency_overrides[get_provider_registry] = lambda: registry
    try:
        response = await client.post(
            "/api/v1/responses",
            json={
                "run_id": run["run_id"],
                "provider": "deepseek",
                "model": "deepseek-v4-flash",
                "input": "Explain visibly",
                "reasoning_display_policy": "provider-visible",
                "stream": True,
            },
        )
    finally:
        await provider_http_client.aclose()

    events = [
        json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")
    ]
    assert [event["type"] for event in events] == [
        "response.started",
        "reasoning.started",
        "reasoning.raw.delta",
        "response.text.delta",
        "response.usage",
        "reasoning.completed",
        "response.completed",
    ]
    assert events[2]["data"]["delta"] == "private reasoning must not be public"
    assert events[-1]["data"]["response"]["reasoning_blocks"][0]["kind"] == "reasoning.raw"

    attempt_id = events[-1]["data"]["response"]["id"]
    saved_message = await client.post(
        f"/api/v1/sessions/{run['session_id']}/messages",
        json={
            "role": "assistant",
            "content_text": "public answer",
            "run_id": run["run_id"],
            "source_model_attempt_id": attempt_id,
        },
    )
    latest_messages = await client.get(f"/api/v1/sessions/{run['session_id']}/messages/latest")
    message_detail = await client.get(f"/api/v1/messages/{saved_message.json()['message_id']}")
    assert saved_message.status_code == 201
    assert saved_message.json()["reasoning_blocks"][0]["kind"] == "reasoning.raw"
    assert latest_messages.json()["items"][-1]["reasoning_blocks"][0]["text"] == (
        "private reasoning must not be public"
    )
    assert message_detail.json()["source_model_attempt_id"] == attempt_id

    second_run = await client.post(
        "/api/v1/runs",
        json={
            "user_id": run["user_id"],
            "session_id": run["session_id"],
            "user_request": "another turn",
        },
    )
    cross_run_message = await client.post(
        f"/api/v1/sessions/{run['session_id']}/messages",
        json={
            "role": "assistant",
            "content_text": "wrong lineage",
            "run_id": second_run.json()["run_id"],
            "source_model_attempt_id": attempt_id,
        },
    )
    assert cross_run_message.status_code == 409


async def test_deepseek_stream_without_done_fails_attempt(
    client: httpx.AsyncClient,
) -> None:
    """Verify a truncated DeepSeek stream emits failure and persists a failed Attempt."""

    run = await create_test_run(client)
    registry, provider_http_client = create_deepseek_stream_registry(include_done_marker=False)
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
        "reasoning.started",
        "response.text.delta",
        "response.usage",
        "reasoning.interrupted",
        "response.failed",
    ]
    assert events[-1]["data"]["error"]["type"] == "response_parse_error"
    assert attempt["status"] == "failed"
    assert attempt["error_code"] == "response_parse_error"


async def test_stream_timeout_emits_failure_and_finalizes_attempt(
    client: httpx.AsyncClient,
) -> None:
    """Enforce the total platform deadline even when an adapter stalls between events."""

    run = await create_test_run(client)
    registry = ProviderRegistry()
    registry.register(
        ProviderName.OPENAI,
        StallingStreamProvider(),
        allowed_models=frozenset({"test-model"}),
    )
    app.dependency_overrides[get_provider_registry] = lambda: registry

    response = await client.post(
        "/api/v1/responses",
        json={
            "run_id": run["run_id"],
            "provider": "openai",
            "model": "test-model",
            "input": "Wait forever",
            "timeout_seconds": 1,
            "stream": True,
        },
    )

    events = [
        json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")
    ]
    run_detail = (await client.get(f"/api/v1/runs/{run['run_id']}")).json()
    assert [event["type"] for event in events] == [
        "response.started",
        "response.failed",
    ]
    assert events[-1]["data"]["error"]["type"] == "timeout"
    assert run_detail["attempts"][0]["status"] == "timed_out"


@pytest.mark.parametrize(
    "chunk",
    [
        {
            "error": {
                "code": "context_length_exceeded",
                "param": "max_tokens",
                "message": "private upstream error with Bearer test-secret",
            }
        },
        {"choices": [{"delta": {"refusal": "private refusal"}, "finish_reason": "stop"}]},
        {"choices": [{"delta": {"content": "partial"}}]},
    ],
)
async def test_chat_stream_failure_persists_failed_attempt_without_completion(client, chunk):
    """Persist safe error diagnostics through ASGI without leaking upstream text to clients."""
    run = await create_test_run(client)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200, text="data: " + json.dumps(chunk) + "\n\ndata: [DONE]\n\n"
            )
        )
    ) as upstream:
        registry = create_provider_registry(
            Settings(_env_file=None, deepseek_api_key="dummy", deepseek_models="deepseek-v4-flash"),
            upstream,
            {ProviderName.DEEPSEEK: "https://offline.invalid"},
        )
        app.dependency_overrides[get_provider_registry] = lambda: registry
        response = await client.post(
            "/api/v1/responses",
            json={
                "run_id": run["run_id"],
                "provider": "deepseek",
                "model": "deepseek-v4-flash",
                "input": "test",
                "stream": True,
            },
        )
    events = [
        json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")
    ]
    assert events[-1]["type"] == "response.failed"
    assert not any(e["type"] == "response.completed" for e in events)
    assert "private" not in response.text
    detail = (await client.get(f"/api/v1/runs/{run['run_id']}")).json()
    assert detail["attempts"][0]["status"] == "failed"
    if "error" in chunk:
        storage = app.dependency_overrides[get_object_storage]()
        uri = detail["attempts"][0]["raw_response_uri"]
        diagnostic = json.loads(storage.objects[uri][0])
        assert diagnostic["provider_error"]["category"] == "context_length_exceeded"
        assert diagnostic["provider_error"]["param"] == "max_tokens"
        assert "private" not in json.dumps(diagnostic)
        assert "test-secret" not in json.dumps(diagnostic)
        assert "provider_error" not in response.text


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("priced", [False, True])
@pytest.mark.parametrize("failure", [None, "storage", "commit_ack", "missing_usage"])
async def test_invalid_json_preserves_usage_and_cost_once(
    client, stream, priced, failure, monkeypatch, test_database_session_factory
):
    """Invalid JSON is failed but billable; replay must not duplicate usage or run cost."""
    original_commit = AsyncSession.commit
    injected = False

    async def lose_commit_ack(db):
        """Simulate a committed failed attempt whose acknowledgement was lost once."""
        nonlocal injected
        inject = not injected and any(
            isinstance(item, LlmModelAttempt) and item.status == AttemptStatus.FAILED
            for item in db.identity_map.values()
        )
        await original_commit(db)
        if inject:
            injected = True
            raise RuntimeError("synthetic lost commit acknowledgement")

    class ErrorStorage(RawResponseFailingObjectStorage):
        """Keep request writes available while failing the invalid-JSON audit write."""

        async def put_bytes(self, object_name, content, content_type):
            """Reject raw-error only, allowing the attempted call to be recorded first."""
            if object_name.endswith("/raw-error.json"):
                raise ObjectStorageError("synthetic unavailable audit")
            return await super().put_bytes(object_name, content, content_type)

    if failure == "commit_ack":
        monkeypatch.setattr(AsyncSession, "commit", lose_commit_ack)
    if failure == "storage":
        app.dependency_overrides[get_object_storage] = ErrorStorage
    run = await create_test_run(client)
    body = {
        "id": "invalid-json-fixture",
        "choices": [
            {
                "delta" if stream else "message": {
                    "content": '{"ok":broken}',
                    "reasoning_content": "private reasoning fixture",
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 100, "completion_tokens": 20, "prompt_cache_hit_tokens": 10},
    }
    if failure == "missing_usage":
        body.pop("usage")
    app.dependency_overrides[get_price_catalog] = lambda: PriceCatalog(
        {
            (ProviderName.DEEPSEEK, "deepseek-v4-flash"): ModelPrice(
                input_per_million=Decimal("10"),
                output_per_million=Decimal("30"),
                cached_input_per_million=Decimal("2"),
            )
        }
        if priced
        else {}
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: (
                httpx.Response(200, text="data: " + json.dumps(body) + "\n\ndata: [DONE]\n\n")
                if stream
                else httpx.Response(200, json=body)
            )
        )
    ) as upstream:
        registry = create_provider_registry(
            Settings(_env_file=None, deepseek_api_key="dummy", deepseek_models="deepseek-v4-flash"),
            upstream,
            {ProviderName.DEEPSEEK: "https://offline.invalid"},
        )
        app.dependency_overrides[get_provider_registry] = lambda: registry
        payload = {
            "run_id": run["run_id"],
            "provider": "deepseek",
            "model": "deepseek-v4-flash",
            "input": "Return JSON",
            "json_object_output": True,
            "stream": stream,
            "idempotency_key": "invalid-json-accounting-fixture",
        }
        response = await client.post("/api/v1/responses", json=payload)
        assert response.status_code == (200 if stream else 502), response.text
        assert "response_parse_error" in response.text
        assert "private reasoning fixture" not in response.text
        replay = await client.post("/api/v1/responses", json={**payload, "stream": False})
        assert replay.status_code == 409
    detail = (await client.get(f"/api/v1/runs/{run['run_id']}")).json()
    assert len(detail["attempts"]) == 1
    attempt = detail["attempts"][0]
    assert attempt["status"] == "failed"
    assert attempt["input_tokens"] == (None if failure == "missing_usage" else 100)
    assert attempt["output_tokens"] == (None if failure == "missing_usage" else 20)
    assert attempt["cached_tokens"] == (None if failure == "missing_usage" else 10)
    cost_known = priced and failure != "missing_usage"
    assert attempt["estimated_cost"] == ("0.001520" if cost_known else None)
    assert detail["cost_used"] == ("0.001520" if cost_known else "0.000000")
    assert attempt["provider_request_id"] is None  # Intentionally private in public DTOs.
    assert len(attempt["retries"]) == 1
    async with test_database_session_factory() as db:
        stored = await db.get(LlmModelAttempt, attempt["attempt_id"])
        assert stored.provider_request_id == "invalid-json-fixture"
        assert (stored.raw_response_uri is None) == (failure == "storage")
    reasoning = await client.get(f"/api/v1/attempts/{attempt['attempt_id']}/reasoning-blocks")
    assert "private reasoning fixture" not in reasoning.text
    if failure == "commit_ack":
        assert injected


async def test_stream_usage_before_error_is_preserved_without_summing_snapshots(client):
    """Retain last known cumulative usage without claiming an incomplete bill is final."""
    run = await create_test_run(client)
    chunks = [
        {"choices": [], "usage": {"prompt_tokens": 100, "completion_tokens": 10}},
        {"choices": [], "usage": {"prompt_tokens": 100, "completion_tokens": 20}},
        {"error": {"code": "server_error", "message": "PRIVATE"}},
    ]
    body = "".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, text=body))
    ) as upstream:
        registry = create_provider_registry(
            Settings(_env_file=None, deepseek_api_key="dummy", deepseek_models="deepseek-v4-flash"),
            upstream,
            {ProviderName.DEEPSEEK: "https://offline.invalid"},
        )
        app.dependency_overrides[get_provider_registry] = lambda: registry
        response = await client.post(
            "/api/v1/responses",
            json={
                "run_id": run["run_id"],
                "provider": "deepseek",
                "model": "deepseek-v4-flash",
                "input": "test",
                "stream": True,
            },
        )
    assert "response.failed" in response.text
    assert "PRIVATE" not in response.text
    detail = (await client.get(f"/api/v1/runs/{run['run_id']}")).json()
    attempt = detail["attempts"][0]
    assert attempt["status"] == "failed"
    assert attempt["input_tokens"] == 100
    assert attempt["output_tokens"] == 20
    assert attempt["estimated_cost"] is None
    storage = app.dependency_overrides[get_object_storage]()
    audit = json.loads(storage.objects[attempt["raw_response_uri"]][0])
    assert audit["usage_complete"] is False


@pytest.mark.parametrize(
    "name,provider_type,stream",
    [
        (ProviderName.OPENAI, OpenAIResponsesProvider, True),
        (ProviderName.ANTHROPIC, AnthropicMessagesProvider, True),
        (ProviderName.OPENAI, OpenAIResponsesProvider, False),
    ],
)
async def test_provider_failure_api_never_exposes_upstream_message(
    client, name, provider_type, stream
):
    """Persist failed attempts and safe diagnostics through the actual API in both entry modes."""
    run = await create_test_run(client)
    error = {"code": "server_error", "message": "PRIVATE_SYNTHETIC", "echo": "PRIVATE_SYNTHETIC"}
    body = {"type": "error", "error": error} if stream else {"status": "failed", "error": error}
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: (
                httpx.Response(200, text="data: " + json.dumps(body) + "\n\n")
                if stream
                else httpx.Response(200, json=body)
            )
        )
    ) as upstream:
        registry = ProviderRegistry()
        registry.register(
            name,
            provider_type(
                HttpTransport(upstream, max_retries=0),
                base_url="https://offline.invalid",
                api_key="dummy",
            ),
            allowed_models={"test"},
        )
        app.dependency_overrides[get_provider_registry] = lambda: registry
        response = await client.post(
            "/api/v1/responses",
            json={
                "run_id": run["run_id"],
                "provider": name.value,
                "model": "test",
                "input": "test",
                "stream": stream,
            },
        )
    assert response.status_code == (200 if stream else 502), response.text
    assert "PRIVATE_SYNTHETIC" not in response.text
    if stream:
        assert "response.failed" in response.text
        assert "response.completed" not in response.text
    detail = (await client.get(f"/api/v1/runs/{run['run_id']}")).json()
    attempt = detail["attempts"][0]
    assert attempt["status"] == "failed"
    storage = app.dependency_overrides[get_object_storage]()
    diagnostic = json.loads(storage.objects[attempt["raw_response_uri"]][0])
    assert "PRIVATE_SYNTHETIC" not in json.dumps(diagnostic)
    assert diagnostic["provider_error"]["category"] == "server_error"


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
    """Reject unsupported parameters and internal-only JSON validation controls at HTTP ingress."""

    run = await create_test_run(client)
    base_payload = {
        "run_id": run["run_id"],
        "provider": "deepseek",
        "model": "deepseek-v4-flash",
        "input": "Hello",
    }

    for field_name in [
        "frequency_penalty",
        "presence_penalty",
        "unknown_option",
        "defer_structured_validation",
        "_defer_structured_validation",
    ]:
        response = await client.post(
            "/api/v1/responses",
            json={**base_payload, field_name: 1},
        )
        assert response.status_code == 422
