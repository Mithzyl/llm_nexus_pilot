"""Minimal Project scope and Project Memory HTTP schemas."""

import enum
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from nexuspilot_api.models import (
    MemoryStatus,
    ProjectMemoryStatus,
    ProjectStatus,
    SessionMemoryStatus,
)
from nexuspilot_api.schemas.base import ApiModel


class ProjectMemoryPolicyAction(str, enum.Enum):
    """Explicit opt-in transitions for Project Memory policy."""

    ENABLE = "enable"
    SUSPEND = "suspend"
    DISABLE = "disable"


class ProjectCreate(BaseModel):
    """Validate creation of the minimal Project scope without auto-enabling Memory."""

    model_config = ConfigDict(extra="forbid")

    owner_user_id: str = Field(min_length=1, max_length=128)
    project_name: str = Field(min_length=1, max_length=255)


class ProjectUpdate(BaseModel):
    """Validate the mutable Project display fields."""

    model_config = ConfigDict(extra="forbid")

    project_name: str | None = Field(default=None, min_length=1, max_length=255)


class ProjectMemoryPolicyUpdate(BaseModel):
    """Validate one explicit Project Memory policy transition."""

    model_config = ConfigDict(extra="forbid")

    action: ProjectMemoryPolicyAction
    actor_id: str | None = Field(default=None, min_length=1, max_length=128)


class ProjectWorkspaceCreate(BaseModel):
    """Validate one credential-free canonical workspace locator."""

    model_config = ConfigDict(extra="forbid")

    workspace_type: str = Field(min_length=1, max_length=64)
    credential_free_locator: str = Field(min_length=1, max_length=1024)


class ProjectWorkspaceRead(ApiModel):
    """Expose one sanitized workspace locator without credential material."""

    project_workspace_id: str
    project_id: str
    workspace_type: str
    credential_free_locator: str
    locator_hash: str
    is_active: bool


class ProjectRead(ApiModel):
    """Expose the minimal Project scope and its explicit Memory policy state."""

    project_id: str
    owner_user_id: str
    project_name: str
    status: ProjectStatus
    memory_status: ProjectMemoryStatus
    memory_enabled_at: datetime | None
    memory_enabled_by_actor_id: str | None
    current_memory_profile_snapshot_id: str | None
    created_at: datetime
    updated_at: datetime


class ProjectMemoryProfileRead(ApiModel):
    """Expose the current immutable Project Profile with token evidence."""

    project_memory_profile_snapshot_id: str
    project_id: str
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


class ProjectMemoryCandidateRead(ApiModel):
    """Expose one Project Memory candidate with its trust and approval state."""

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
    expires_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ProjectProfileRebuildCreate(BaseModel):
    """Validate one optimistic Project Profile rebuild from current formal facts."""

    model_config = ConfigDict(extra="forbid")

    expected_previous_version: int = Field(default=0, ge=0)
    tokenizer_name: Literal["utf8_upper_bound"] = "utf8_upper_bound"
    tokenizer_version: Literal["utf8_bytes_upper_bound_v1"] = "utf8_bytes_upper_bound_v1"
    idempotency_key: str = Field(min_length=1, max_length=128)
