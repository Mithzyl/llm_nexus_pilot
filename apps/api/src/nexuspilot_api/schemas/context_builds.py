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
    run_id: str | None = Field(default=None, min_length=1, max_length=36)
    current_user_message_id: str | None = Field(default=None, min_length=1, max_length=36)
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=128)
    token_budget: int | None = Field(default=None, ge=256, le=200_000)
    reserved_output_tokens: int = Field(default=0, ge=0, le=100_000)
    system_instruction: str | None = Field(default=None, min_length=1, max_length=100_000)
    additional_user_input: str | None = Field(default=None, min_length=1, max_length=100_000)
    recent_message_count: int = Field(default=12, ge=1, le=100)

    @model_validator(mode="after")
    def validate_reserved_output_budget(self) -> "ContextBuildCreate":
        """Reject a reservation that leaves no room for input context."""

        runtime_fields = (
            self.run_id,
            self.current_user_message_id,
            self.idempotency_key,
        )
        if any(runtime_fields) and not all(runtime_fields):
            raise ValueError(
                "run_id, current_user_message_id, and idempotency_key must be provided together"
            )
        if self.additional_user_input is not None and self.run_id is None:
            raise ValueError("additional_user_input is available only for runtime context")
        if (
            self.token_budget is not None
            and self.reserved_output_tokens >= self.token_budget
        ):
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
    run_id: str | None
    current_user_message_id: str | None
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
