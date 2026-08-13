"""Contract tests for provider codecs, registry routing, retries, and pricing."""

import json
from decimal import Decimal

import httpx
import pytest

from nexuspilot_models.contracts import (
    Message,
    MessageRole,
    ModelRequest,
    ProviderName,
    ReasoningConfiguration,
    ReasoningEffort,
    ToolDefinition,
)
from nexuspilot_models.errors import ModelProviderError
from nexuspilot_models.pricing import ModelPrice, PriceCatalog
from nexuspilot_models.providers.anthropic import AnthropicMessagesProvider
from nexuspilot_models.providers.deepseek import DeepSeekChatProvider
from nexuspilot_models.providers.gemini import GeminiGenerateContentProvider
from nexuspilot_models.providers.openai import OpenAIResponsesProvider
from nexuspilot_models.providers.openai_compatible import OpenAICompatibleChatProvider
from nexuspilot_models.registry import ProviderRegistry
from nexuspilot_models.transport import HttpTransport


def model_request(provider: ProviderName, model: str = "test-model") -> ModelRequest:
    """Build a minimal portable request shared across provider contract tests."""

    return ModelRequest(
        provider=provider,
        model=model,
        messages=[Message(role=MessageRole.USER, content="Hello")],
    )


@pytest.mark.parametrize(
    ("provider_name", "sse_body"),
    [
        (
            ProviderName.OPENAI,
            'data: {"type":"response.output_text.delta","delta":"hello"}\n\n'
            'data: {"type":"response.completed","response":{"id":"resp-1",'
            '"status":"completed","output":[{"type":"message","content":[{'
            '"type":"output_text","text":"hello"}]}],"usage":{"input_tokens":2,'
            '"output_tokens":1}}}\n\n',
        ),
        (
            ProviderName.DEEPSEEK,
            'data: {"id":"chat-1","choices":[{"delta":{"content":"hello"},'
            '"finish_reason":"stop"}]}\n\n'
            'data: {"choices":[],"usage":{"prompt_tokens":2,"completion_tokens":1}}\n\n'
            "data: [DONE]\n\n",
        ),
        (
            ProviderName.ANTHROPIC,
            'event: message_start\ndata: {"type":"message_start","message":{"id":'
            '"msg-1","usage":{"input_tokens":2}}}\n\n'
            'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,'
            '"delta":{"type":"text_delta","text":"hello"}}\n\n'
            'event: message_delta\ndata: {"type":"message_delta","delta":{"stop_reason":'
            '"end_turn"},"usage":{"output_tokens":1}}\n\n'
            'event: message_stop\ndata: {"type":"message_stop"}\n\n',
        ),
        (
            ProviderName.GEMINI,
            'data: {"responseId":"gem-1","candidates":[{"content":{"parts":[{'
            '"text":"hello"}]},"finishReason":"STOP"}],"usageMetadata":{'
            '"promptTokenCount":2,"candidatesTokenCount":1}}\n\n',
        ),
    ],
)
async def test_all_provider_streams_share_public_event_contract(
    provider_name: ProviderName,
    sse_body: str,
) -> None:
    """Verify every official adapter emits started, text, and completed public events."""

    async def handler(_request: httpx.Request) -> httpx.Response:
        """Return provider-specific SSE bytes without contacting an external service."""

        return httpx.Response(
            200,
            content=sse_body.encode(),
            headers={"Content-Type": "text/event-stream"},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    transport = HttpTransport(client, max_retries=0)
    if provider_name is ProviderName.OPENAI:
        provider = OpenAIResponsesProvider(
            transport, base_url="https://provider.example/v1", api_key="key"
        )
    elif provider_name is ProviderName.DEEPSEEK:
        provider = DeepSeekChatProvider(
            transport=transport,
            base_url="https://provider.example/v1",
            api_key="key",
        )
    elif provider_name is ProviderName.ANTHROPIC:
        provider = AnthropicMessagesProvider(
            transport, base_url="https://provider.example/v1", api_key="key"
        )
    else:
        provider = GeminiGenerateContentProvider(
            transport, base_url="https://provider.example/v1", api_key="key"
        )

    events = [event async for event in provider.stream(model_request(provider_name))]
    await client.aclose()

    assert events[0].type.value == "response.started"
    assert any(event.type.value == "response.text.delta" for event in events)
    assert events[-1].type.value == "response.completed"
    assert events[-1].data["response"]["text"] == "hello"


@pytest.mark.parametrize(
    ("provider_name", "response_payload", "expected_path", "expected_text"),
    [
        (
            ProviderName.DEEPSEEK,
            {
                "id": "chat-1",
                "choices": [{"message": {"content": "deep"}, "finish_reason": "stop"}],
                "usage": {
                    "prompt_tokens": 3,
                    "completion_tokens": 2,
                    "prompt_cache_hit_tokens": 1,
                },
            },
            "/chat/completions",
            "deep",
        ),
        (
            ProviderName.OPENAI_COMPATIBLE,
            {
                "id": "local-1",
                "choices": [{"message": {"content": "local"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 2, "completion_tokens": 1},
            },
            "/chat/completions",
            "local",
        ),
    ],
)
async def test_openai_compatible_provider_reuses_chat_codec(
    provider_name: ProviderName,
    response_payload: dict,
    expected_path: str,
    expected_text: str,
) -> None:
    """Verify DeepSeek and custom compatible endpoints share one configurable codec."""

    async def handler(request: httpx.Request) -> httpx.Response:
        """Assert the compatible wire shape and return a provider-style completion."""

        assert request.url.path.endswith(expected_path)
        body = json.loads(request.content)
        assert body["messages"][-1] == {"role": "user", "content": "Hello"}
        return httpx.Response(200, json=response_payload)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OpenAICompatibleChatProvider(
        name=provider_name,
        transport=HttpTransport(client, max_retries=0),
        base_url="https://provider.example/v1",
        api_key="test-key",
        supports_json_schema=False,
    )
    response = await provider.generate(model_request(provider_name))
    await client.aclose()

    assert response.text == expected_text
    assert response.provider_request_id in {"chat-1", "local-1"}
    assert len(response.transport_attempts) == 1


async def test_compatible_provider_rejects_undeclared_json_schema_support() -> None:
    """Verify adapters never silently weaken JSON Schema to plain JSON mode."""

    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: None))
    provider = OpenAICompatibleChatProvider(
        name=ProviderName.DEEPSEEK,
        transport=HttpTransport(client, max_retries=0),
        base_url="https://provider.example/v1",
        api_key="test-key",
        supports_json_schema=False,
    )
    request = model_request(ProviderName.DEEPSEEK).model_copy(
        update={"output_schema": {"type": "object"}}
    )

    with pytest.raises(ModelProviderError, match="does not declare JSON Schema") as caught:
        await provider.generate(request)
    await client.aclose()

    assert caught.value.error_type == "unsupported_capability"


@pytest.mark.parametrize(
    ("model", "effort"),
    [
        ("deepseek-v4-flash", ReasoningEffort.LOW),
        ("deepseek-v4-flash", ReasoningEffort.HIGH),
        ("deepseek-v4-flash", ReasoningEffort.MAX),
        ("deepseek-v4-pro", ReasoningEffort.HIGH),
        ("deepseek-v4-pro", ReasoningEffort.MAX),
    ],
)
async def test_deepseek_maps_supported_reasoning_configuration(
    model: str,
    effort: ReasoningEffort,
) -> None:
    """Verify supported reasoning modes use the current DeepSeek Chat Completions wire shape."""

    async def handler(request: httpx.Request) -> httpx.Response:
        """Assert DeepSeek never receives a Responses API request or nested effort field."""

        assert request.url.path == "/chat/completions"
        body = json.loads(request.content)
        assert body["thinking"] == {"type": "enabled"}
        assert body["reasoning_effort"] == effort.value
        assert "reasoning_effort" not in body["thinking"]
        return httpx.Response(
            200,
            json={
                "id": "deepseek-response",
                "choices": [{"message": {"content": "answer"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 2, "completion_tokens": 1},
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = DeepSeekChatProvider(
        HttpTransport(client, max_retries=0),
        base_url="https://api.deepseek.com",
        api_key="test-key",
    )
    request = model_request(ProviderName.DEEPSEEK, model).model_copy(
        update={
            "reasoning": ReasoningConfiguration(enabled=True, effort=effort),
        }
    )

    response = await provider.generate(request)
    await client.aclose()

    assert response.text == "answer"


def test_reasoning_configuration_rejects_effort_when_disabled() -> None:
    """Verify disabled reasoning cannot carry an effort that would have no effect."""

    with pytest.raises(ValueError, match="effort"):
        ReasoningConfiguration(enabled=False, effort=ReasoningEffort.HIGH)


async def test_deepseek_rejects_low_effort_for_v4_pro_before_network() -> None:
    """Verify the platform does not silently let DeepSeek map v4-pro low effort to high."""

    request_count = 0

    async def handler(_request: httpx.Request) -> httpx.Response:
        """Record an unexpected provider request made after local capability validation."""

        nonlocal request_count
        request_count += 1
        return httpx.Response(500)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = DeepSeekChatProvider(
        HttpTransport(client, max_retries=0),
        base_url="https://api.deepseek.com",
        api_key="test-key",
    )
    request = model_request(ProviderName.DEEPSEEK, "deepseek-v4-pro").model_copy(
        update={
            "reasoning": ReasoningConfiguration(
                enabled=True,
                effort=ReasoningEffort.LOW,
            )
        }
    )

    with pytest.raises(ModelProviderError, match="does not support reasoning effort") as caught:
        await provider.generate(request)
    await client.aclose()

    assert caught.value.error_type == "unsupported_capability"
    assert request_count == 0


@pytest.mark.parametrize(
    "reasoning",
    [None, ReasoningConfiguration(enabled=True)],
)
async def test_deepseek_rejects_temperature_when_reasoning_is_enabled(
    reasoning: ReasoningConfiguration | None,
) -> None:
    """Verify an ignored sampling parameter fails locally instead of creating false semantics."""

    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: None))
    provider = DeepSeekChatProvider(
        HttpTransport(client, max_retries=0),
        base_url="https://api.deepseek.com",
        api_key="test-key",
    )
    request = model_request(ProviderName.DEEPSEEK, "deepseek-v4-flash").model_copy(
        update={
            "temperature": 0.5,
            "reasoning": reasoning,
        }
    )

    with pytest.raises(ModelProviderError, match="temperature") as caught:
        await provider.generate(request)
    await client.aclose()

    assert caught.value.error_type == "invalid_request"


async def test_deepseek_disabled_reasoning_allows_temperature() -> None:
    """Verify non-thinking DeepSeek requests retain supported sampling controls."""

    async def handler(request: httpx.Request) -> httpx.Response:
        """Assert disabled thinking and temperature are both represented explicitly."""

        body = json.loads(request.content)
        assert body["thinking"] == {"type": "disabled"}
        assert body["temperature"] == 0.5
        assert "reasoning_effort" not in body
        return httpx.Response(
            200,
            json={
                "id": "deepseek-response",
                "choices": [{"message": {"content": "answer"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 2, "completion_tokens": 1},
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = DeepSeekChatProvider(
        HttpTransport(client, max_retries=0),
        base_url="https://api.deepseek.com",
        api_key="test-key",
    )
    request = model_request(ProviderName.DEEPSEEK, "deepseek-v4-flash").model_copy(
        update={
            "temperature": 0.5,
            "reasoning": ReasoningConfiguration(enabled=False),
        }
    )

    await provider.generate(request)
    await client.aclose()


async def test_deepseek_stream_requires_done_and_retains_reasoning_evidence() -> None:
    """Verify completed streams retain private reasoning evidence only after a DONE marker."""

    sse_body = (
        'data: {"id":"chat-1","choices":[{"delta":{"reasoning_content":"plan"},'
        '"finish_reason":null}]}\n\n'
        'data: {"id":"chat-1","choices":[{"delta":{"content":"answer"},'
        '"finish_reason":"stop"}]}\n\n'
        'data: {"choices":[],"usage":{"prompt_tokens":2,"completion_tokens":1}}\n\n'
        "data: [DONE]\n\n"
    )

    async def handler(_request: httpx.Request) -> httpx.Response:
        """Return a complete DeepSeek reasoning stream."""

        return httpx.Response(
            200,
            content=sse_body.encode(),
            headers={"Content-Type": "text/event-stream"},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = DeepSeekChatProvider(
        HttpTransport(client, max_retries=0),
        base_url="https://api.deepseek.com",
        api_key="test-key",
    )

    events = [
        event
        async for event in provider.stream(
            model_request(ProviderName.DEEPSEEK, "deepseek-v4-flash")
        )
    ]
    await client.aclose()

    assert [event.type.value for event in events] == [
        "response.started",
        "response.text.delta",
        "response.usage",
        "response.completed",
    ]
    raw_response = events[-1].data["response"]["raw_response"]
    assert raw_response["reasoning_content"] == "plan"
    assert events[-1].data["response"]["text"] == "answer"


async def test_deepseek_stream_without_done_fails() -> None:
    """Verify a truncated DeepSeek stream never becomes a successful completed response."""

    async def handler(_request: httpx.Request) -> httpx.Response:
        """Return a stream that closes without the required DONE marker."""

        return httpx.Response(
            200,
            content=(
                'data: {"id":"chat-1","choices":[{"delta":{"content":"partial"},'
                '"finish_reason":"stop"}]}\n\n'
            ).encode(),
            headers={"Content-Type": "text/event-stream"},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = DeepSeekChatProvider(
        HttpTransport(client, max_retries=0),
        base_url="https://api.deepseek.com",
        api_key="test-key",
    )

    with pytest.raises(ModelProviderError, match="DONE") as caught:
        _events = [
            event
            async for event in provider.stream(
                model_request(ProviderName.DEEPSEEK, "deepseek-v4-flash")
            )
        ]
    await client.aclose()

    assert caught.value.error_type == "response_parse_error"


async def test_deepseek_rejects_strict_tools_before_network() -> None:
    """Verify undeclared DeepSeek strict-tool support cannot reach the normal endpoint."""

    request_count = 0

    async def handler(_request: httpx.Request) -> httpx.Response:
        """Record an unexpected request made before strict-tool capability validation."""

        nonlocal request_count
        request_count += 1
        return httpx.Response(500)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = DeepSeekChatProvider(
        HttpTransport(client, max_retries=0),
        base_url="https://api.deepseek.com",
        api_key="test-key",
    )
    request = model_request(ProviderName.DEEPSEEK, "deepseek-v4-flash").model_copy(
        update={
            "tools": [
                ToolDefinition(
                    name="lookup",
                    description="Look up one record.",
                    input_schema={"type": "object"},
                    strict=True,
                )
            ]
        }
    )

    with pytest.raises(ModelProviderError, match="strict tool") as caught:
        await provider.generate(request)
    await client.aclose()

    assert caught.value.error_type == "unsupported_capability"
    assert request_count == 0


async def test_other_providers_reject_unimplemented_reasoning_configuration() -> None:
    """Verify a portable reasoning request is never silently ignored by another adapter."""

    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: None))
    transport = HttpTransport(client, max_retries=0)
    providers = [
        (
            OpenAIResponsesProvider(
                transport,
                base_url="https://api.openai.com/v1",
                api_key="test-key",
            ),
            ProviderName.OPENAI,
        ),
        (
            AnthropicMessagesProvider(
                transport,
                base_url="https://api.anthropic.com/v1",
                api_key="test-key",
            ),
            ProviderName.ANTHROPIC,
        ),
        (
            GeminiGenerateContentProvider(
                transport,
                base_url="https://generativelanguage.googleapis.com/v1beta",
                api_key="test-key",
            ),
            ProviderName.GEMINI,
        ),
    ]

    for provider, provider_name in providers:
        request = model_request(provider_name).model_copy(
            update={"reasoning": ReasoningConfiguration(enabled=True)}
        )
        with pytest.raises(ModelProviderError) as caught:
            await provider.generate(request)
        assert caught.value.error_type == "unsupported_capability"
    await client.aclose()


async def test_openai_responses_codec_parses_text_tools_and_usage() -> None:
    """Verify OpenAI native Responses payloads are normalized independently of chat format."""

    async def handler(request: httpx.Request) -> httpx.Response:
        """Return a representative OpenAI Responses object."""

        assert request.url.path == "/v1/responses"
        return httpx.Response(
            200,
            json={
                "id": "resp-1",
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": "hello"}],
                    },
                    {
                        "type": "function_call",
                        "call_id": "call-1",
                        "name": "lookup",
                        "arguments": '{"id": 7}',
                    },
                ],
                "usage": {
                    "input_tokens": 10,
                    "output_tokens": 4,
                    "input_tokens_details": {"cached_tokens": 3},
                },
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OpenAIResponsesProvider(
        HttpTransport(client, max_retries=0),
        base_url="https://api.openai.com/v1",
        api_key="test-key",
    )
    response = await provider.generate(model_request(ProviderName.OPENAI))
    await client.aclose()

    assert response.text == "hello"
    assert response.tool_calls[0].arguments == {"id": 7}
    assert response.cached_tokens == 3


async def test_anthropic_messages_codec_parses_content_blocks() -> None:
    """Verify Anthropic text and tool_use blocks map to the common response contract."""

    async def handler(_request: httpx.Request) -> httpx.Response:
        """Return a representative Anthropic Message object."""

        return httpx.Response(
            200,
            json={
                "id": "msg-1",
                "content": [
                    {"type": "text", "text": "hello"},
                    {
                        "type": "tool_use",
                        "id": "tool-1",
                        "name": "lookup",
                        "input": {"id": 7},
                    },
                ],
                "stop_reason": "tool_use",
                "usage": {"input_tokens": 6, "output_tokens": 3},
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = AnthropicMessagesProvider(
        HttpTransport(client, max_retries=0),
        base_url="https://api.anthropic.com/v1",
        api_key="test-key",
    )
    response = await provider.generate(model_request(ProviderName.ANTHROPIC))
    await client.aclose()

    assert response.finish_reason == "tool_calls"
    assert response.tool_calls[0].name == "lookup"


async def test_gemini_codec_parses_candidate_parts() -> None:
    """Verify Gemini candidate text, functionCall, usage, and response ID are normalized."""

    async def handler(request: httpx.Request) -> httpx.Response:
        """Return a representative Gemini GenerateContent response."""

        assert request.url.path.endswith("/models/test-model:generateContent")
        return httpx.Response(
            200,
            json={
                "responseId": "gem-1",
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {"text": "hello"},
                                {
                                    "functionCall": {
                                        "id": "call-1",
                                        "name": "lookup",
                                        "args": {"id": 7},
                                    }
                                },
                            ]
                        },
                        "finishReason": "STOP",
                    }
                ],
                "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 2},
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = GeminiGenerateContentProvider(
        HttpTransport(client, max_retries=0),
        base_url="https://generativelanguage.googleapis.com/v1beta",
        api_key="test-key",
    )
    response = await provider.generate(model_request(ProviderName.GEMINI))
    await client.aclose()

    assert response.provider_request_id == "gem-1"
    assert response.tool_calls[0].arguments == {"id": 7}


async def test_transport_retries_rate_limit_and_retains_physical_evidence() -> None:
    """Verify retryable status codes create one trace per physical request."""

    call_count = 0

    async def handler(_request: httpx.Request) -> httpx.Response:
        """Rate-limit the first call and succeed on the second."""

        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return httpx.Response(429, json={"error": {"message": "slow down"}})
        return httpx.Response(200, json={"ok": True})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    result = await HttpTransport(client, max_retries=1, backoff_seconds=0).post_json(
        "https://provider.example/test",
        headers={},
        payload={},
        timeout_seconds=1,
    )
    await client.aclose()

    assert result.payload == {"ok": True}
    assert [item.status_code for item in result.attempts] == [429, 200]


def test_registry_validates_provider_model_combination() -> None:
    """Verify an allowlist prevents a model name from being routed to the wrong provider."""

    registry = ProviderRegistry()
    provider = object()
    registry.register(
        ProviderName.OPENAI,
        provider,  # type: ignore[arg-type]
        allowed_models=frozenset({"allowed-model"}),
    )

    assert registry.resolve(ProviderName.OPENAI, "allowed-model") is provider
    with pytest.raises(Exception, match="not allowed"):
        registry.resolve(ProviderName.OPENAI, "other-model")


def test_price_catalog_uses_cached_input_rate() -> None:
    """Verify explicit cached-token prices replace only the cached portion of input usage."""

    catalog = PriceCatalog(
        {
            (ProviderName.OPENAI, "priced-model"): ModelPrice(
                input_per_million=Decimal("10"),
                output_per_million=Decimal("30"),
                cached_input_per_million=Decimal("2"),
            )
        }
    )

    cost = catalog.estimate(
        ProviderName.OPENAI,
        "priced-model",
        input_tokens=1_000_000,
        output_tokens=1_000_000,
        cached_tokens=500_000,
    )

    assert cost == Decimal("36.000000")


def test_price_catalog_maximum_estimate_uses_the_more_expensive_input_rate() -> None:
    """Verify budget reservations remain conservative when cached input costs more."""

    catalog = PriceCatalog(
        {
            (ProviderName.OPENAI, "priced-model"): ModelPrice(
                input_per_million=Decimal("1"),
                output_per_million=Decimal("3"),
                cached_input_per_million=Decimal("2"),
            )
        }
    )

    cost = catalog.estimate_maximum(
        ProviderName.OPENAI,
        "priced-model",
        input_tokens_upper_bound=2,
        output_tokens_upper_bound=1,
    )

    assert cost == Decimal("0.000007")
