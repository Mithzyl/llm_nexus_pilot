"""Context Build and per-source selection evidence HTTP schemas."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from nexuspilot_api.models import ContextBuildStatus, ContextSourceSelectionStatus
from nexuspilot_api.schemas.base import ApiModel


class ContextBuildCreate(BaseModel):
    """Validate one version-pinned Context Build for a later model call."""

    model_config = ConfigDict(extra="forbid")

    user_id: str = Field(min_length=1, max_length=128)
    session_id: str = Field(min_length=1, max_length=36)
    provider: str = Field(min_length=1, max_length=64)
    model: str = Field(min_length=1, max_length=128)
    catalog_version: str | None = Field(default=None, min_length=1, max_length=64)
    token_budget: int = Field(ge=256, le=200_000)
    reserved_output_tokens: int = Field(default=0, ge=0, le=100_000)
    system_instruction: str | None = Field(default=None, min_length=1, max_length=4_000)
    recent_message_count: int = Field(default=12, ge=1, le=100)

    @model_validator(mode="after")
    def validate_reserved_output_budget(self) -> "ContextBuildCreate":
        """Reject a reservation that leaves no room for input context."""

        if self.reserved_output_tokens >= self.token_budget:
            raise ValueError("reserved_output_tokens must be less than token_budget")
        return self


class ContextSourceRead(ApiModel):
    """Expose one selected or excluded source with version and reason evidence."""

    source_type: str
    source_id: str
    source_version: str | None
    token_estimate: int
    selection_status: ContextSourceSelectionStatus
    exclusion_reason: str | None
    content_hash: str | None


class ContextMessageRead(ApiModel):
    """Expose one assembled context message with its owning source reference."""

    role: str
    content: str
    source_type: str
    source_id: str
    source_version: str | None


class ContextBuildRead(ApiModel):
    """Expose one persisted Context Build and its complete selection evidence."""

    context_build_id: str
    catalog_version_id: str
    user_id: str
    session_id: str | None
    project_id: str | None
    provider: str
    model: str
    token_budget: int
    reserved_output_tokens: int
    recent_message_count: int
    tokenizer_name: str
    tokenizer_version: str
    input_token_estimate: int
    status: ContextBuildStatus
    messages: list[ContextMessageRead]
    sources: list[ContextSourceRead]
    created_at: datetime
