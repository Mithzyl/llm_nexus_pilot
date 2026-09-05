"""Core application configuration loaded from environment variables."""

from functools import lru_cache
from pathlib import Path
from typing import Self
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

API_ENV_FILE = Path(__file__).resolve().parents[3] / ".env"


class Settings(BaseSettings):
    """Define validated runtime settings for the API and infrastructure clients."""

    model_config = SettingsConfigDict(
        env_file=API_ENV_FILE,
        env_prefix="NEXUSPILOT_",
        case_sensitive=False,
        extra="ignore",
    )

    environment: str = "development"
    api_key: str = Field(default="local-development-key-change-me", min_length=16)
    internal_api_key: SecretStr = Field(
        default=SecretStr("local-internal-key-change-me"),
        min_length=16,
    )
    cursor_signing_key: SecretStr | None = Field(default=None, min_length=16)
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
    provider_continuation_encryption_key: SecretStr | None = Field(
        default=None,
        min_length=32,
    )
    provider_continuation_retention_seconds: int = Field(
        default=86_400,
        ge=60,
        le=2_592_000,
    )
    authentication_cookie_secure: bool = False
    authentication_allowed_origins: str = "http://localhost:3000,http://127.0.0.1:3000"
    authentication_session_idle_seconds: int = Field(default=43_200, ge=300, le=604_800)
    authentication_session_absolute_seconds: int = Field(
        default=2_592_000,
        ge=3_600,
        le=7_776_000,
    )
    authentication_login_failure_limit: int = Field(default=5, ge=1, le=50)
    authentication_login_failure_window_seconds: int = Field(
        default=900,
        ge=60,
        le=86_400,
    )
    openai_api_key: SecretStr | None = None
    openai_models: str = ""

    deepseek_api_key: SecretStr | None = None
    deepseek_models: str = ""

    anthropic_api_key: SecretStr | None = None
    anthropic_models: str = ""

    gemini_api_key: SecretStr | None = None
    gemini_models: str = ""

    openai_compatible_api_key: SecretStr | None = None
    openai_compatible_models: str = ""

    @model_validator(mode="after")
    def validate_separate_internal_key(self) -> Self:
        """Reject configurations that collapse public and internal trust boundaries."""

        if self.api_key == self.internal_api_key.get_secret_value():
            raise ValueError("internal_api_key must differ from api_key")
        if (
            self.environment.lower() not in {"development", "test"}
            and self.provider_continuation_encryption_key is None
        ):
            raise ValueError("provider_continuation_encryption_key is required outside development")
        if self.authentication_session_absolute_seconds < self.authentication_session_idle_seconds:
            raise ValueError(
                "authentication_session_absolute_seconds must be greater than or equal to "
                "authentication_session_idle_seconds"
            )
        allowed_origins = self.allowed_authentication_origins()
        if not allowed_origins:
            raise ValueError("authentication_allowed_origins must contain at least one origin")
        for allowed_origin in allowed_origins:
            parsed_origin = urlsplit(allowed_origin)
            if (
                parsed_origin.scheme not in {"http", "https"}
                or not parsed_origin.hostname
                or parsed_origin.username is not None
                or parsed_origin.password is not None
                or parsed_origin.path not in {"", "/"}
                or parsed_origin.query
                or parsed_origin.fragment
            ):
                raise ValueError(
                    "authentication_allowed_origins must contain only absolute HTTP(S) origins"
                )
        if self.environment.lower() not in {"development", "test"}:
            if not self.authentication_cookie_secure:
                raise ValueError("authentication_cookie_secure must be enabled outside development")
            if any(not origin.startswith("https://") for origin in allowed_origins):
                raise ValueError(
                    "authentication_allowed_origins must use HTTPS outside development"
                )
        return self

    def continuation_encryption_secret(self) -> str:
        """Return the dedicated secret or the development-only internal-key fallback."""

        if self.provider_continuation_encryption_key is not None:
            return self.provider_continuation_encryption_key.get_secret_value()
        return self.internal_api_key.get_secret_value()

    def allowed_authentication_origins(self) -> frozenset[str]:
        """Return the normalized exact origins allowed to mutate Cookie sessions."""

        return frozenset(
            origin.strip().rstrip("/")
            for origin in self.authentication_allowed_origins.split(",")
            if origin.strip()
        )


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide validated settings instance."""

    return Settings()
