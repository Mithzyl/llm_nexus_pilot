"""Verify Strata credentials, database endpoint gating and shared chat serialization."""

import json

import httpx
import pytest
from nexuspilot_models.contracts import ModelRequest, ProviderName

from nexuspilot_api.core.config import Settings
from nexuspilot_api.infrastructure.provider_registry import create_provider_registry


async def test_strata_registered_model_uses_provider_default_without_fake_effort(
    test_database_session_factory,
):
    """An unconfigured control profile permits provider defaults but rejects explicit controls."""
    from nexuspilot_models.contracts import ReasoningConfiguration
    from nexuspilot_models.errors import ModelProviderError

    from nexuspilot_api.models import LlmRegisteredModel
    from nexuspilot_api.services.model_reasoning_policy_service import (
        resolve_registered_model_reasoning_configuration,
    )

    async with test_database_session_factory() as db:
        db.add(
            LlmRegisteredModel(
                provider="strata", model_identifier="test-model", display_name="Strata test"
            )
        )
        await db.commit()
        args = dict(provider=ProviderName.STRATA, model="test-model", require_registration=True)
        assert await resolve_registered_model_reasoning_configuration(
            db,
            **args,
            requested_reasoning=None,
        ) == (None, None)
        with pytest.raises(ModelProviderError):
            await resolve_registered_model_reasoning_configuration(
                db,
                **args,
                requested_reasoning=ReasoningConfiguration(enabled=True, effort="medium"),
            )


@pytest.mark.parametrize("key,endpoint", [(None, True), ("test-strata-key", False)])
async def test_strata_requires_key_and_enabled_endpoint(key, endpoint):
    """Do not advertise Strata unless both independent configuration sources exist."""
    name = ProviderName("strata")
    async with httpx.AsyncClient() as client:
        registry = create_provider_registry(
            Settings(_env_file=None, strata_api_key=key, strata_models="test-model"),
            client,
            {name: "https://strata.example/v1"} if endpoint else {},
        )
        assert name not in registry.registered_names


@pytest.mark.parametrize(
    "body",
    [
        'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n',
        "data: [DONE]\n\n",
    ],
)
async def test_strata_collected_stream_rejects_incomplete_or_empty_output(body):
    """Neither a disconnected partial response nor a bare DONE is successful output."""
    from nexuspilot_models.errors import ModelProviderError

    def incomplete(request):
        """Return a valid text delta followed by an unexpected stream EOF."""
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, text=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(incomplete)) as client:
        registry = create_provider_registry(
            Settings(_env_file=None, strata_api_key="test-strata-key", strata_models="test-model"),
            client,
            {ProviderName.STRATA: "https://strata.example/v1"},
        )
        with pytest.raises(ModelProviderError):
            await registry.resolve(ProviderName.STRATA, "test-model").generate(
                ModelRequest(
                    provider=ProviderName.STRATA,
                    model="test-model",
                    messages=[{"role": "user", "content": "Hi"}],
                )
            )


async def test_strata_uses_chat_endpoint_and_does_not_advertise_unverified_schema():
    """Send credentials only as bearer headers and preserve the endpoint's v1 path."""
    name = ProviderName("strata")
    requests = []

    def respond(request):
        """Capture the actual codec request and return a standard Chat response."""
        requests.append(request)
        chunk = {
            "id": "chat-test",
            "choices": [{"delta": {"content": "OK"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
        }
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            text="data: " + json.dumps(chunk) + "\n\ndata: [DONE]\n\n",
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        registry = create_provider_registry(
            Settings(_env_file=None, strata_api_key="test-strata-key", strata_models="test-model"),
            client,
            {name: "https://strata.example/v1/"},
        )
        adapter = registry.resolve(name, "test-model")
        assert not adapter.supports_json_schema
        result = await adapter.generate(
            ModelRequest(
                provider=name,
                model="test-model",
                messages=[{"role": "user", "content": "Hi"}],
            )
        )
        assert result.text == "OK"
        assert result.input_tokens == 5
        assert result.output_tokens == 2
        assert json.loads(requests[0].content)["stream"] is True
        assert str(requests[0].url) == "https://strata.example/v1/chat/completions"
        assert requests[0].headers["authorization"] == "Bearer test-strata-key"
        assert "test-strata-key" not in json.dumps(json.loads(requests[0].content))
