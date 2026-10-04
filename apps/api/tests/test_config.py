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
        "authentication_enabled": True,
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


def test_login_defaults_off_but_cannot_be_disabled_in_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Permit explicit single-user local operation, never an anonymous production deployment."""

    monkeypatch.delenv("NEXUSPILOT_AUTHENTICATION_ENABLED", raising=False)
    assert Settings(_env_file=None).authentication_enabled is False
    with pytest.raises(ValidationError, match="authentication_enabled"):
        Settings(
            _env_file=None,
            environment="production",
            authentication_enabled=False,
            provider_continuation_encryption_key=SecretStr("c" * 32),
            authentication_cookie_secure=True,
            authentication_allowed_origins="https://nexus.example.com",
        )


def test_authentication_session_absolute_lifetime_cannot_precede_idle_lifetime() -> None:
    """Reject session settings whose absolute expiry is shorter than the idle expiry."""

    with pytest.raises(ValidationError, match="must be greater than or equal"):
        Settings(
            authentication_session_idle_seconds=7_200,
            authentication_session_absolute_seconds=3_600,
        )


def production_settings():
    """Return explicit synthetic production credentials independent of local environment files."""
    return dict(
        environment="production",
        authentication_enabled=True,
        authentication_cookie_secure=True,
        authentication_allowed_origins="https://chat.example.test",
        api_key="a" * 64,
        internal_api_key="b" * 64,
        cursor_signing_key="c" * 64,
        provider_continuation_encryption_key="d" * 64,
        database_url="mysql+aiomysql://app:" + "e" * 64 + "@mysql/nexuspilot",
        minio_access_key="app-files",
        minio_secret_key="f" * 64,
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("api_key", "local-development-key-change-me"),
        ("internal_api_key", "local-internal-key-change-me"),
        ("api_key", "test-api-key-long-enough"),
        ("cursor_signing_key", None),
        ("database_url", "mysql+aiomysql://nexuspilot:nexuspilot@mysql/nexuspilot"),
        ("database_url", "mysql+aiomysql://app@mysql/nexuspilot"),
        ("minio_secret_key", "local-development-secret-change-me"),
    ],
)
def test_production_rejects_known_or_missing_credentials(field, value):
    """A deployment cannot silently inherit sample service keys or database/storage passwords."""
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{**production_settings(), field: value})


def test_production_accepts_explicit_separate_credentials():
    """Accept deployment-injected credentials without requiring secrets to live in source."""
    assert Settings(_env_file=None, **production_settings()).environment == "production"
