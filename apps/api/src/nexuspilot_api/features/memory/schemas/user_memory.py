"""L4 User Memory Profile and candidate approval HTTP schemas."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from nexuspilot_api.models import MemoryStatus, SessionMemoryStatus
from nexuspilot_api.schemas.base import ApiModel


class UserMemoryProfileRebuildCreate(BaseModel):
    """Validate one optimistic User Profile rebuild from current formal facts."""

    model_config = ConfigDict(extra="forbid")

    expected_previous_version: int = Field(default=0, ge=0)
    tokenizer_name: Literal["utf8_upper_bound"] = "utf8_upper_bound"
    tokenizer_version: Literal["utf8_bytes_upper_bound_v1"] = "utf8_bytes_upper_bound_v1"
    idempotency_key: str = Field(min_length=1, max_length=128)


class UserMemoryProfileRead(ApiModel):
    """Expose the current immutable User Profile with token evidence."""

    user_memory_profile_snapshot_id: str
    user_id: str
    schema_version: str
    version: int
    status: SessionMemoryStatus
    profile_json: dict
    estimated_token_count: int
    tokenizer_name: str
    tokenizer_version: str
    json_snapshot_object_id: str | None
    markdown_snapshot_object_id: str | None
    activated_at: datetime | None
    failed_at: datetime | None
    error_code: str | None


class UserMemoryCandidateRead(ApiModel):
    """Expose one User Memory candidate with trust and approval evidence."""

    memory_id: str
    memory_type: str
    status: MemoryStatus
    version_number: int
    content_text: str | None
    semantic_key: str | None
    importance: str
    confidence: str
    approval_method: str
    approved_at: datetime | None
    sensitivity_classification: str
    is_core_profile_eligible: bool
    expires_at: datetime | None
    created_at: datetime
    updated_at: datetime


class CandidateDecisionCreate(BaseModel):
    """Validate an approval or rejection decision with optimistic concurrency."""

    model_config = ConfigDict(extra="forbid")

    expected_version_number: int = Field(ge=1)
    idempotency_key: str = Field(min_length=1, max_length=128)
    actor_id: str | None = Field(default=None, min_length=1, max_length=128)
