"""Core application configuration loaded from environment variables."""

from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Define validated runtime settings for the API and infrastructure clients."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="NEXUSPILOT_",
        case_sensitive=False,
        extra="ignore",
    )

    environment: str = "development"
    api_key: str = Field(default="local-development-key-change-me", min_length=16)
    database_url: str = "mysql+aiomysql://nexuspilot:nexuspilot@localhost:3306/nexuspilot"
    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = "nexuspilot"
    minio_secret_key: str = "local-development-secret-change-me"
    minio_bucket: str = "nexuspilot-artifacts"
    minio_secure: bool = False
    max_artifact_size_bytes: int = Field(default=20 * 1024 * 1024, ge=1)
    model_max_retries: int = Field(default=2, ge=0, le=5)
    model_retry_backoff_seconds: float = Field(default=0.1, ge=0, le=10)
    model_pricing_json: str = "{}"

    openai_api_key: SecretStr | None = None
    openai_base_url: str = "https://api.openai.com/v1"
    openai_models: str = ""

    deepseek_api_key: SecretStr | None = None
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_models: str = ""

    anthropic_api_key: SecretStr | None = None
    anthropic_base_url: str = "https://api.anthropic.com/v1"
    anthropic_models: str = ""

    gemini_api_key: SecretStr | None = None
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    gemini_models: str = ""

    openai_compatible_api_key: SecretStr | None = None
    openai_compatible_base_url: str | None = None
    openai_compatible_models: str = ""


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide validated settings instance."""

    return Settings()
