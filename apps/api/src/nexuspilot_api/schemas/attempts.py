"""Model-attempt request and response schemas."""

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from nexuspilot_api.models import AttemptStatus
from nexuspilot_api.schemas.base import ApiModel


class AttemptCreate(BaseModel):
    """Validate one completed, failed, or in-progress provider call record."""

    task_id: str | None = None
    provider: str = Field(min_length=1, max_length=64)
    model: str = Field(min_length=1, max_length=128)
    request_type: str = Field(default="generation", min_length=1, max_length=64)
    status: AttemptStatus
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    cached_tokens: int | None = Field(default=None, ge=0)
    estimated_cost: Decimal | None = Field(default=None, ge=0)
    latency_ms: int | None = Field(default=None, ge=0)
    provider_request_id: str | None = Field(default=None, max_length=255)
    raw_request_uri: str | None = Field(default=None, max_length=1024)
    raw_response_uri: str | None = Field(default=None, max_length=1024)
    error_code: str | None = Field(default=None, max_length=128)
    error_message: str | None = None
    completed_at: datetime | None = None


class AttemptRetryRead(ApiModel):
    """Expose one physical HTTP request made within a logical model attempt."""

    retry_id: str
    attempt_id: str
    attempt_index: int
    status_code: int | None
    latency_ms: int
    error_type: str | None
    error_message: str | None
    created_at: datetime


class AttemptRead(ApiModel):
    """Expose immutable accounting and diagnostic data for a model call."""

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
    estimated_cost: Decimal | None
    latency_ms: int | None
    provider_request_id: str | None
    raw_request_uri: str | None
    raw_response_uri: str | None
    error_code: str | None
    error_message: str | None
    started_at: datetime
    completed_at: datetime | None
    retries: list[AttemptRetryRead] = Field(default_factory=list)
