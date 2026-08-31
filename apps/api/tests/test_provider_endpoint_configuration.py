"""Provider endpoint persistence, URL validation, and startup-loading contracts."""

import pytest
from nexuspilot_models.contracts import ProviderName
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from nexuspilot_api.models import LlmProviderEndpoint
from nexuspilot_api.services.provider_endpoint_service import (
    ProviderEndpointConfigurationError,
    load_enabled_provider_base_urls,
    validate_provider_base_url,
)


@pytest.mark.parametrize(
    ("base_url", "expected"),
    [
        ("https://api.deepseek.com", "https://api.deepseek.com"),
        (
            "https://gateway.example.com:8443/provider/v1/",
            "https://gateway.example.com:8443/provider/v1",
        ),
        ("http://localhost:11434/v1", "http://localhost:11434/v1"),
    ],
)
def test_validate_provider_base_url_accepts_http_hosts_ports_and_paths(
    base_url: str,
    expected: str,
) -> None:
    """Accept hierarchical HTTP endpoints and normalize only a trailing slash."""

    assert validate_provider_base_url(base_url) == expected


@pytest.mark.parametrize(
    "base_url",
    [
        "ftp://api.example.com/v1",
        "https://user:password@api.example.com/v1",
        "https:///v1",
        "https://api.example.com:invalid/v1",
        "https://api.example.com:0/v1",
        "https://api.example.com/v1?token=secret",
        "https://api.example.com/v1#section",
    ],
)
def test_validate_provider_base_url_rejects_unsafe_or_ambiguous_urls(
    base_url: str,
) -> None:
    """Reject unsupported schemes, credentials, invalid authorities, and URL suffix data."""

    with pytest.raises(ProviderEndpointConfigurationError):
        validate_provider_base_url(base_url)


async def test_load_enabled_provider_base_urls_returns_only_enabled_rows(
    test_database_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Load one validated URL per enabled Provider and ignore disabled history."""

    async with test_database_session_factory() as database_session:
        database_session.add_all(
            [
                LlmProviderEndpoint(
                    provider=ProviderName.DEEPSEEK,
                    base_url="https://gateway.example.com:8443/deepseek/v1/",
                    enabled=True,
                ),
                LlmProviderEndpoint(
                    provider=ProviderName.OPENAI,
                    base_url="https://disabled.example.com/v1",
                    enabled=False,
                ),
            ]
        )
        await database_session.commit()

        loaded_base_urls = await load_enabled_provider_base_urls(database_session)

    assert loaded_base_urls == {
        ProviderName.DEEPSEEK: "https://gateway.example.com:8443/deepseek/v1"
    }


async def test_load_enabled_provider_base_urls_rejects_invalid_persisted_url(
    test_database_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Fail startup loading when persisted endpoint data violates the URL contract."""

    async with test_database_session_factory() as database_session:
        database_session.add(
            LlmProviderEndpoint(
                provider=ProviderName.DEEPSEEK,
                base_url="ftp://unsafe.example.com",
                enabled=True,
            )
        )
        await database_session.commit()

        with pytest.raises(ProviderEndpointConfigurationError):
            await load_enabled_provider_base_urls(database_session)
