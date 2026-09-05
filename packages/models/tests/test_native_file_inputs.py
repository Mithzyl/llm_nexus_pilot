"""Native input contracts preserve bytes and fail before dispatch on unsupported routes."""

import base64
import hashlib
import json

import httpx
import pytest
from nexuspilot_models.contracts import Message, ModelRequest, ProviderName
from nexuspilot_models.errors import ModelProviderError
from nexuspilot_models.providers.anthropic import AnthropicMessagesProvider
from nexuspilot_models.providers.deepseek import DeepSeekChatProvider
from nexuspilot_models.providers.gemini import GeminiGenerateContentProvider
from nexuspilot_models.providers.openai import OpenAIResponsesProvider
from nexuspilot_models.providers.openai_compatible import OpenAICompatibleChatProvider
from nexuspilot_models.transport import HttpTransport


def native_request(
    provider: ProviderName, *, media_type: str = "application/pdf"
) -> ModelRequest:
    """Build a trusted fixture with an explicit exact-model native-input policy."""

    content = (
        b"%PDF-1.7\nfixture"
        if media_type == "application/pdf"
        else b"\x89PNG\r\n\x1a\nfixture"
    )
    return ModelRequest.model_validate(
        {
            "provider": provider,
            "model": "fixture-model",
            "native_input_capabilities": {
                "media_types": [media_type],
                "max_file_bytes": 1024,
                "max_request_bytes": 2048,
                "max_files": 2,
                "input_token_reserve_per_file": 1024,
            },
            "messages": [
                {
                    "role": "user",
                    "content": "Describe the attached source.",
                    "attachments": [
                        {
                            "filename": "source.pdf"
                            if media_type == "application/pdf"
                            else "source.png",
                            "media_type": media_type,
                            "data_base64": base64.b64encode(content).decode(),
                            "content_hash": hashlib.sha256(content).hexdigest(),
                        }
                    ],
                }
            ],
        }
    )


@pytest.mark.parametrize(
    "provider_name", [ProviderName.OPENAI, ProviderName.ANTHROPIC, ProviderName.GEMINI]
)
@pytest.mark.parametrize("media_type", ["application/pdf", "image/png"])
async def test_native_codecs_preserve_content_without_storage_urls(
    provider_name, media_type
) -> None:
    """Assert native PDF/image payloads include original bytes, never platform object locators."""

    request = native_request(provider_name, media_type=media_type)
    async with httpx.AsyncClient() as client:
        transport = HttpTransport(client=client)
        provider_class = {
            ProviderName.OPENAI: OpenAIResponsesProvider,
            ProviderName.ANTHROPIC: AnthropicMessagesProvider,
            ProviderName.GEMINI: GeminiGenerateContentProvider,
        }[provider_name]
        adapter = provider_class(
            transport, base_url="https://provider.test", api_key="fixture"
        )
        adapter._validate_request(request)
        if provider_name == ProviderName.GEMINI:
            payload = adapter._build_payload(request)
            block = payload["contents"][0]["parts"][1]["inlineData"]
            assert block == {
                "mimeType": media_type,
                "data": request.messages[0].attachments[0].data_base64,
            }
        else:
            payload = adapter._build_payload(request, stream=False)
            if provider_name == ProviderName.OPENAI:
                block = payload["input"][0]["content"][1]
                key = "file_data" if media_type == "application/pdf" else "image_url"
                assert (
                    block[key]
                    == f"data:{media_type};base64,{request.messages[0].attachments[0].data_base64}"
                )
            else:
                block = payload["messages"][0]["content"][1]
                assert block["source"] == {
                    "type": "base64",
                    "media_type": media_type,
                    "data": request.messages[0].attachments[0].data_base64,
                }


@pytest.mark.parametrize(
    "provider_name", [ProviderName.DEEPSEEK, ProviderName.OPENAI_COMPATIBLE]
)
async def test_unimplemented_native_routes_reject_before_http(provider_name) -> None:
    """Compatibility branding cannot silently drop files or claim native support."""

    async with httpx.AsyncClient() as client:
        transport = HttpTransport(client=client)
        adapter = (
            DeepSeekChatProvider(
                transport, base_url="https://provider.test", api_key="fixture"
            )
            if provider_name == ProviderName.DEEPSEEK
            else OpenAICompatibleChatProvider(
                name=provider_name,
                transport=transport,
                base_url="https://provider.test",
                api_key="fixture",
            )
        )
        with pytest.raises(ModelProviderError, match="native"):
            await adapter.generate(native_request(provider_name))


def test_native_contract_rejects_unknown_capability_roles_and_unverified_bytes() -> (
    None
):
    """Files require explicit capability evidence, user role, valid encoding, hash and MIME."""

    request = native_request(ProviderName.OPENAI)
    payload = request.model_dump()
    payload.pop("native_input_capabilities", None)
    with pytest.raises(ValueError):
        ModelRequest.model_validate(payload)
    attachment = request.messages[0].attachments[0].model_dump()
    for changes in [
        {"data_base64": "not base64"},
        {"data_base64": attachment["data_base64"] + "===="},
        {"content_hash": "0" * 64},
        {"media_type": "image/jpeg"},
        {"filename": "../secret.pdf"},
        {"file_url": "http://localhost/private"},
    ]:
        with pytest.raises(ValueError):
            Message(role="user", content="read", attachments=[attachment | changes])
    with pytest.raises(ValueError):
        Message(role="assistant", content="result", attachments=[attachment])


def test_native_policy_enforces_request_totals_and_keeps_text_compatible() -> None:
    """Resource bounds apply across messages; legacy text still needs no native profile."""

    request = native_request(ProviderName.OPENAI)
    payload = request.model_dump()
    payload["native_input_capabilities"] = {
        "media_types": ["image/png"],
        "max_file_bytes": 1024,
        "max_request_bytes": 2048,
        "max_files": 2,
        "input_token_reserve_per_file": 1024,
    }
    with pytest.raises(ValueError):
        ModelRequest.model_validate(payload)
    payload["native_input_capabilities"]["media_types"] = ["application/pdf"]
    payload["messages"] = payload["messages"] * 3
    with pytest.raises(ValueError):
        ModelRequest.model_validate(payload)
    payload["messages"] = payload["messages"][:1]
    payload["native_input_capabilities"]["max_file_bytes"] = 1
    with pytest.raises(ValueError, match="size"):
        ModelRequest.model_validate(payload)
    size = request.messages[0].attachments[0].size_bytes
    payload["native_input_capabilities"].update(max_file_bytes=size, max_request_bytes=size)
    payload["messages"] *= 2
    with pytest.raises(ValueError, match="request exceeds"):
        ModelRequest.model_validate(payload)
    assert ModelRequest(
        provider="openai",
        model="text",
        messages=[Message(role="user", content="hello")],
    )


@pytest.mark.parametrize(
    "provider_name", [ProviderName.OPENAI, ProviderName.ANTHROPIC, ProviderName.GEMINI]
)
async def test_native_streams_transmit_files_and_finish_normally(provider_name) -> None:
    """Exercise real HTTP serialization and SSE decoding with fixture transport, not paid providers."""

    events = {
        ProviderName.OPENAI: 'data: {"type":"response.completed","response":{"id":"r",'
        '"status":"completed","output":[],"usage":{"input_tokens":1,"output_tokens":0}}}\n\n',
        ProviderName.ANTHROPIC: 'event: message_start\ndata: {"type":"message_start","message":'
        '{"id":"r","usage":{"input_tokens":1}}}\n\nevent: message_delta\ndata: '
        '{"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":0}}'
        '\n\nevent: message_stop\ndata: {"type":"message_stop"}\n\n',
        ProviderName.GEMINI: 'data: {"responseId":"r","candidates":[{"content":{"parts":[]},'
        '"finishReason":"STOP"}],"usageMetadata":{"promptTokenCount":1,"candidatesTokenCount":0}}\n\n',
    }
    bodies = []

    def respond(http_request: httpx.Request) -> httpx.Response:
        """Capture wire JSON and return one provider-specific successful stream."""

        bodies.append(json.loads(http_request.content))
        return httpx.Response(
            200,
            text=events[provider_name],
            headers={"content-type": "text/event-stream"},
        )

    request = native_request(provider_name)
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        adapter_class = {
            ProviderName.OPENAI: OpenAIResponsesProvider,
            ProviderName.ANTHROPIC: AnthropicMessagesProvider,
            ProviderName.GEMINI: GeminiGenerateContentProvider,
        }[provider_name]
        adapter = adapter_class(
            HttpTransport(client, max_retries=0),
            base_url="https://provider.test",
            api_key="fixture",
        )
        results = [event async for event in adapter.stream(request)]
    assert results[-1].type.value == "response.completed"
    assert len(bodies) == 1
    assert request.messages[0].attachments[0].data_base64 in json.dumps(bodies[0])
