"""Configuration boundary tests for service-local environment files."""

from pathlib import Path

from nexuspilot_api.core.config import API_ENV_FILE


def test_api_environment_file_is_anchored_to_api_directory() -> None:
    """Ensure API settings never depend on the process working directory."""

    expected_api_env_file = Path(__file__).resolve().parents[1] / ".env"

    assert API_ENV_FILE == expected_api_env_file
