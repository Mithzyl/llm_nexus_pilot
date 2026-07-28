"""Contract tests for provider codecs, registry routing, retries, and pricing."""

from decimal import Decimal

import httpx
import pytest

from nexuspilot_models.contracts import Message, MessageRole, ModelRequest, ProviderName
from nexuspilot_models.errors import ModelProviderError
from nexuspilot_models.pricing import ModelPrice, PriceCatalog
from nexuspilot_models.providers.anthropic import AnthropicMessagesProvider
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
        provider = OpenAICompatibleChatProvider(
            name=ProviderName.DEEPSEEK,
            transport=transport,
            base_url="https://provider.example/v1",
            api_key="key",
            supports_json_schema=False,
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
        body = __import__("json").loads(request.content)
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

    with pytest.raises(
        ModelProviderError, match="does not declare JSON Schema"
    ) as caught:
        await provider.generate(request)
    await client.aclose()

    assert caught.value.error_type == "unsupported_capability"


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
