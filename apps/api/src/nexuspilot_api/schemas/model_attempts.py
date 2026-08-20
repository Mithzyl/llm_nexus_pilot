"""LLM model invocation and provider transport-attempt API schemas."""

from datetime import datetime
from decimal import Decimal

from nexuspilot_models.contracts import ReasoningDisplayPolicy
from pydantic import BaseModel, Field

from nexuspilot_api.models import AttemptStatus
from nexuspilot_api.schemas.base import ApiModel


class ModelAttemptCreate(BaseModel):
    """Validate one completed, failed, or in-progress LLM model invocation."""

    task_id: str | None = None
    provider: str = Field(min_length=1, max_length=64)
    model: str = Field(min_length=1, max_length=128)
    request_type: str = Field(default="generation", min_length=1, max_length=64)
    status: AttemptStatus
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    cached_tokens: int | None = Field(default=None, ge=0)
    reasoning_tokens: int | None = Field(default=None, ge=0)
    reasoning_display_policy: ReasoningDisplayPolicy = ReasoningDisplayPolicy.HIDDEN
    estimated_cost: Decimal | None = Field(default=None, ge=0)
    latency_ms: int | None = Field(default=None, ge=0)
    provider_request_id: str | None = Field(default=None, max_length=255)
    raw_request_uri: str | None = Field(default=None, max_length=1024)
    raw_response_uri: str | None = Field(default=None, max_length=1024)
    error_code: str | None = Field(default=None, max_length=128)
    error_message: str | None = None
    completed_at: datetime | None = None


class ModelTransportAttemptRead(ApiModel):
    """Expose one provider HTTP request within a logical LLM model invocation."""

    retry_id: str
    attempt_id: str
    attempt_index: int
    status_code: int | None
    latency_ms: int
    error_type: str | None
    error_message: str | None
    created_at: datetime


class ModelAttemptRead(ApiModel):
    """Expose immutable accounting and diagnostics for an LLM model invocation."""

    attempt_id: str
    run_id: str
    task_id: str | None
    provider: str
    model: str
    request_type: str
    request_key: str | None
    retry_count: int
    status: AttemptStatus
    input_tokens: int | None
    output_tokens: int | None
    cached_tokens: int | None
    reasoning_tokens: int | None
    reasoning_display_policy: ReasoningDisplayPolicy
    estimated_cost: Decimal | None
    latency_ms: int | None
    provider_request_id: str | None
    raw_request_uri: str | None
    raw_response_uri: str | None
    error_code: str | None
    error_message: str | None
    started_at: datetime
    first_visible_token_at: datetime | None
    completed_at: datetime | None
    retries: list[ModelTransportAttemptRead] = Field(default_factory=list)


class ModelAttemptSummary(ApiModel):
    """Expose bounded model invocation accounting without raw object locations."""

    attempt_id: str
    run_id: str
    task_id: str | None
    provider: str
    model: str
    request_type: str
    retry_count: int
    status: AttemptStatus
    input_tokens: int | None
    output_tokens: int | None
    cached_tokens: int | None
    reasoning_tokens: int | None
    reasoning_display_policy: ReasoningDisplayPolicy
    estimated_cost: Decimal | None
    latency_ms: int | None
    error_code: str | None
    started_at: datetime
    first_visible_token_at: datetime | None
    completed_at: datetime | None


class ModelAttemptDetail(ModelAttemptSummary):
    """Expose safe model invocation diagnostics without internal storage URIs."""

    request_key: str | None
    provider_request_id: str | None
    error_message_preview: str | None
    has_raw_request: bool
    has_raw_response: bool


class ModelTransportAttemptSummary(ApiModel):
    """Expose one provider HTTP transport attempt with a bounded error preview."""

    retry_id: str
    attempt_id: str
    attempt_index: int
    status_code: int | None
    latency_ms: int
    error_type: str | None
    error_message_preview: str | None
    created_at: datetime


class ModelTransportAttemptDetail(ModelTransportAttemptSummary):
    """Expose the safe public representation of one provider transport attempt."""
