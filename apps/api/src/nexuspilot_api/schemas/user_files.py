"""Browser-visible private file fields; object addresses and ownership cannot be supplied."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from nexuspilot_api.schemas.base import ApiModel


class FileUploadCapabilities(BaseModel):
    """Public upload availability; never include executor paths, image IDs or credentials."""

    accepted_extensions: list[str]
    executor_status: Literal["available", "not_configured", "unavailable", "busy"]
    max_file_size_bytes: int = 20 * 1024 * 1024
    unavailable_reason: str | None = None


class UserFileCreate(BaseModel):
    """Reserve declared bytes and hash before accepting an immutable upload."""

    model_config = ConfigDict(extra="forbid")
    filename: str = Field(min_length=1, max_length=255)
    size_bytes: int = Field(gt=0, le=20 * 1024 * 1024, strict=True)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    idempotency_key: str = Field(min_length=8, max_length=128)


class UserFileRead(ApiModel):
    """Expose lifecycle facts without keys, storage locations or parsed private content."""

    file_id: str
    filename: str
    mime_type: str
    size_bytes: int
    status: str
    error: str | None
    run_id: str | None
    parse_summary: dict = Field(default_factory=dict)
