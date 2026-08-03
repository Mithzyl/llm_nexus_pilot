"""Context Build and per-source selection evidence HTTP schemas."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from nexuspilot_api.models import ContextBuildStatus, ContextSourceSelectionStatus
from nexuspilot_api.schemas.base import ApiModel


class ContextBuildCreate(BaseModel):
    """Validate one version-pinned Context Build for a later model call."""

    model_config = ConfigDict(extra="forbid")

    user_id: str = Field(min_length=1, max_length=128)
    session_id: str | None = Field(default=None, min_length=1, max_length=36)
    project_id: str | None = Field(default=None, min_length=1, max_length=36)
    run_id: str | None = Field(default=None, min_length=1, max_length=36)
    task_id: str | None = Field(default=None, min_length=1, max_length=36)
    agent_run_id: str | None = Field(default=None, min_length=1, max_length=36)
    provider: str = Field(min_length=1, max_length=64)
    model: str = Field(min_length=1, max_length=128)
    token_budget: int = Field(ge=256, le=200_000)
    reserved_output_tokens: int = Field(default=0, ge=0, le=100_000)
    system_instruction: str | None = Field(default=None, min_length=1, max_length=4_000)
    include_memory: bool = True
    include_knowledge: bool = True
    knowledge_query: str | None = Field(default=None, min_length=1, max_length=2_000)
    recent_message_count: int = Field(default=12, ge=1, le=100)
    tokenizer_name: str = Field(min_length=1, max_length=64)
    tokenizer_version: str = Field(min_length=1, max_length=64)


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
    user_id: str
    session_id: str | None
    project_id: str | None
    provider: str
    model: str
    token_budget: int
    tokenizer_name: str
    tokenizer_version: str
    input_token_estimate: int
    status: ContextBuildStatus
    messages: list[ContextMessageRead]
    sources: list[ContextSourceRead]
    created_at: datetime
