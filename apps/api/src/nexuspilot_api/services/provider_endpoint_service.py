"""Validate and load database-managed Provider endpoint addresses at startup."""

from urllib.parse import urlsplit

from nexuspilot_models.contracts import ProviderName
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nexuspilot_api.models.provider_endpoint import LlmProviderEndpoint


class ProviderEndpointConfigurationError(RuntimeError):
    """Report invalid persisted Provider endpoint configuration during startup."""


def validate_provider_base_url(base_url: str) -> str:
    """Validate an HTTP(S) endpoint root and return it without trailing slashes."""

    normalized_base_url = base_url.strip()
    if not normalized_base_url or any(character.isspace() for character in normalized_base_url):
        raise ProviderEndpointConfigurationError(
            "Provider Base URL must be a non-empty URL without whitespace."
        )
    try:
        parsed_url = urlsplit(normalized_base_url)
        parsed_port = parsed_url.port
    except ValueError as exc:
        raise ProviderEndpointConfigurationError(
            "Provider Base URL contains an invalid host or port."
        ) from exc
    if parsed_url.scheme.lower() not in {"http", "https"}:
        raise ProviderEndpointConfigurationError("Provider Base URL must use http or https.")
    if not parsed_url.hostname:
        raise ProviderEndpointConfigurationError("Provider Base URL must include a host.")
    if parsed_url.username is not None or parsed_url.password is not None:
        raise ProviderEndpointConfigurationError(
            "Provider Base URL must not include a username or password."
        )
    if parsed_port == 0:
        raise ProviderEndpointConfigurationError(
            "Provider Base URL port must be between 1 and 65535."
        )
    if parsed_url.query or parsed_url.fragment:
        raise ProviderEndpointConfigurationError(
            "Provider Base URL must not include a query string or fragment."
        )
    return normalized_base_url.rstrip("/")


async def load_enabled_provider_base_urls(
    database_session: AsyncSession,
) -> dict[ProviderName, str]:
    """Load and validate the single enabled Base URL for every configured Provider."""

    query_result = await database_session.scalars(
        select(LlmProviderEndpoint)
        .where(LlmProviderEndpoint.enabled.is_(True))
        .order_by(LlmProviderEndpoint.provider, LlmProviderEndpoint.provider_endpoint_id)
    )
    enabled_base_urls: dict[ProviderName, str] = {}
    for provider_endpoint in query_result:
        provider_name = ProviderName(provider_endpoint.provider)
        if provider_name in enabled_base_urls:
            raise ProviderEndpointConfigurationError(
                f"Provider '{provider_name.value}' has more than one enabled Base URL."
            )
        enabled_base_urls[provider_name] = validate_provider_base_url(provider_endpoint.base_url)
    return enabled_base_urls
