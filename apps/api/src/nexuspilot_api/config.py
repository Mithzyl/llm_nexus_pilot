"""Application configuration loaded from environment variables."""

from functools import lru_cache

from pydantic import Field
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


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide validated settings instance."""

    return Settings()
