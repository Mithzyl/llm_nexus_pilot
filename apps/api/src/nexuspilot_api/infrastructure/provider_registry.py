"""Application composition root for provider adapters and pricing configuration."""

import json
from decimal import Decimal, InvalidOperation

import httpx
from nexuspilot_models.contracts import ProviderName
from nexuspilot_models.pricing import ModelPrice, PriceCatalog
from nexuspilot_models.providers.anthropic import AnthropicMessagesProvider
from nexuspilot_models.providers.deepseek import DeepSeekChatProvider
from nexuspilot_models.providers.gemini import GeminiGenerateContentProvider
from nexuspilot_models.providers.openai import OpenAIResponsesProvider
from nexuspilot_models.providers.openai_compatible import OpenAICompatibleChatProvider
from nexuspilot_models.registry import ProviderRegistry
from nexuspilot_models.transport import HttpTransport

from nexuspilot_api.core.config import Settings


def create_provider_registry(
    settings: Settings,
    http_client: httpx.AsyncClient,
) -> ProviderRegistry:
    """Create explicitly configured adapters around one shared HTTP transport."""

    transport = HttpTransport(
        http_client,
        max_retries=settings.model_max_retries,
        backoff_seconds=settings.model_retry_backoff_seconds,
    )
    registry = ProviderRegistry()
    if settings.openai_api_key:
        registry.register(
            ProviderName.OPENAI,
            OpenAIResponsesProvider(
                transport,
                base_url=settings.openai_base_url,
                api_key=settings.openai_api_key.get_secret_value(),
            ),
            allowed_models=_parse_model_allowlist(settings.openai_models),
        )
    if settings.deepseek_api_key:
        registry.register(
            ProviderName.DEEPSEEK,
            DeepSeekChatProvider(
                transport=transport,
                base_url=settings.deepseek_base_url,
                api_key=settings.deepseek_api_key.get_secret_value(),
            ),
            allowed_models=_parse_model_allowlist(settings.deepseek_models),
        )
    if settings.anthropic_api_key:
        registry.register(
            ProviderName.ANTHROPIC,
            AnthropicMessagesProvider(
                transport,
                base_url=settings.anthropic_base_url,
                api_key=settings.anthropic_api_key.get_secret_value(),
            ),
            allowed_models=_parse_model_allowlist(settings.anthropic_models),
        )
    if settings.gemini_api_key:
        registry.register(
            ProviderName.GEMINI,
            GeminiGenerateContentProvider(
                transport,
                base_url=settings.gemini_base_url,
                api_key=settings.gemini_api_key.get_secret_value(),
            ),
            allowed_models=_parse_model_allowlist(settings.gemini_models),
        )
    if settings.openai_compatible_base_url:
        registry.register(
            ProviderName.OPENAI_COMPATIBLE,
            OpenAICompatibleChatProvider(
                name=ProviderName.OPENAI_COMPATIBLE,
                transport=transport,
                base_url=settings.openai_compatible_base_url,
                api_key=(
                    settings.openai_compatible_api_key.get_secret_value()
                    if settings.openai_compatible_api_key
                    else ""
                ),
            ),
            allowed_models=_parse_model_allowlist(settings.openai_compatible_models),
        )
    return registry


def create_price_catalog(settings: Settings) -> PriceCatalog:
    """Parse explicit JSON prices without supplying potentially stale built-in prices."""

    try:
        raw_prices = json.loads(settings.model_pricing_json)
    except json.JSONDecodeError as exc:
        raise ValueError("NEXUSPILOT_MODEL_PRICING_JSON must contain valid JSON") from exc
    if not isinstance(raw_prices, dict):
        raise ValueError("NEXUSPILOT_MODEL_PRICING_JSON must contain a JSON object")
    prices: dict[tuple[ProviderName, str], ModelPrice] = {}
    for key, value in raw_prices.items():
        if not isinstance(key, str) or "/" not in key or not isinstance(value, dict):
            raise ValueError("Price entries must use 'provider/model' object keys")
        provider_text, model = key.split("/", 1)
        try:
            provider = ProviderName(provider_text)
            prices[(provider, model)] = ModelPrice(
                input_per_million=Decimal(str(value["input_per_million"])),
                output_per_million=Decimal(str(value["output_per_million"])),
                cached_input_per_million=(
                    Decimal(str(value["cached_input_per_million"]))
                    if value.get("cached_input_per_million") is not None
                    else None
                ),
            )
        except (KeyError, InvalidOperation, ValueError) as exc:
            raise ValueError(f"Invalid model price entry '{key}'") from exc
    return PriceCatalog(prices)


def _parse_model_allowlist(value: str) -> frozenset[str]:
    """Convert a comma-delimited environment setting into exact model identifiers."""

    return frozenset(item.strip() for item in value.split(",") if item.strip())
