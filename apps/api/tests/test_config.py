"""Configuration boundary tests for service-local environment files."""

from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from nexuspilot_api.core.config import API_ENV_FILE, Settings


def test_api_environment_file_is_anchored_to_api_directory() -> None:
    """Ensure API settings never depend on the process working directory."""

    expected_api_env_file = Path(__file__).resolve().parents[1] / ".env"

    assert API_ENV_FILE == expected_api_env_file


def test_provider_base_urls_are_not_runtime_environment_settings() -> None:
    """Keep Provider endpoint addresses outside the API environment contract."""

    provider_base_url_fields = {
        "openai_base_url",
        "deepseek_base_url",
        "anthropic_base_url",
        "gemini_base_url",
        "openai_compatible_base_url",
    }

    assert provider_base_url_fields.isdisjoint(Settings.model_fields)


def test_production_authentication_requires_secure_cookie_and_https_origin() -> None:
    """Reject production browser authentication that could transmit a Cookie over HTTP."""

    common_settings = {
        "environment": "production",
        "provider_continuation_encryption_key": SecretStr("c" * 32),
    }
    with pytest.raises(ValidationError, match="authentication_cookie_secure"):
        Settings(**common_settings, authentication_allowed_origins="https://nexus.example.com")
    with pytest.raises(ValidationError, match="must use HTTPS"):
        Settings(
            **common_settings,
            authentication_cookie_secure=True,
            authentication_allowed_origins="http://nexus.example.com",
        )


def test_authentication_session_absolute_lifetime_cannot_precede_idle_lifetime() -> None:
    """Reject session settings whose absolute expiry is shorter than the idle expiry."""

    with pytest.raises(ValidationError, match="must be greater than or equal"):
        Settings(
            authentication_session_idle_seconds=7_200,
            authentication_session_absolute_seconds=3_600,
        )
