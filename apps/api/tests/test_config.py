"""Configuration boundary tests for service-local environment files."""

from pathlib import Path

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
