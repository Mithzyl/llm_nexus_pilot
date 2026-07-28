"""Validated HTTP request and response contracts for the phase-one API."""

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from nexuspilot_api.models import AttemptStatus, RunStatus, TaskStatus


class ApiModel(BaseModel):
    """Enable ORM-to-response conversion for all public API models."""

    model_config = ConfigDict(from_attributes=True)


class UserCreate(BaseModel):
    """Validate a basic platform identity created by a trusted API caller."""

    user_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.-]+$")
    display_name: str = Field(min_length=1, max_length=255)


class UserRead(ApiModel):
    """Expose the stable identity and activation state of a platform user."""

    user_id: str
    display_name: str
    is_active: bool
    created_at: datetime
    updated_at: datetime


class RunCreate(BaseModel):
    """Validate the user request used to create a run."""

    user_request: str = Field(min_length=1, max_length=100_000)
    user_id: str = Field(min_length=1, max_length=128)
    session_id: str | None = Field(default=None, max_length=128)
    run_type: str = Field(default="general", min_length=1, max_length=64)
    budget_limit: Decimal | None = Field(default=None, ge=0)


class RunRead(ApiModel):
    """Expose persisted run state without embedding potentially large child records."""

    run_id: str
    user_id: str
    session_id: str | None
    user_request: str
    run_type: str
    status: RunStatus
    budget_limit: Decimal | None
    cost_used: Decimal
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class TaskCreate(BaseModel):
    """Validate a concrete task and optional prerequisite identifiers."""

    parent_task_id: str | None = None
    task_type: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=255)
    objective: str = Field(min_length=1, max_length=100_000)
    assigned_role: str | None = Field(default=None, max_length=64)
    priority: int = Field(default=0, ge=-100, le=100)
    max_attempts: int = Field(default=3, ge=1, le=20)
    timeout_seconds: int = Field(default=300, ge=1, le=86_400)
    depends_on_task_ids: list[str] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def validate_unique_dependencies(self) -> "TaskCreate":
        """Reject duplicate dependency identifiers before database insertion."""

        if len(self.depends_on_task_ids) != len(set(self.depends_on_task_ids)):
            raise ValueError("depends_on_task_ids contains duplicates")
        return self


class TaskRead(ApiModel):
    """Expose the current state and execution limits of one task."""

    task_id: str
    run_id: str
    parent_task_id: str | None
    task_type: str
    title: str
    objective: str
    assigned_role: str | None
    status: TaskStatus
    priority: int
    max_attempts: int
    current_attempt: int
    timeout_seconds: int
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime


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
    retries: list["AttemptRetryRead"] = Field(default_factory=list)


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


class ArtifactRead(ApiModel):
    """Expose metadata and object location for an uploaded artifact."""

    artifact_id: str
    run_id: str
    task_id: str | None
    artifact_type: str
    filename: str
    mime_type: str
    content_hash: str
    storage_uri: str
    size_bytes: int
    created_at: datetime


class RunDetail(RunRead):
    """Expose a run together with its current task tree and model attempts."""

    tasks: list[TaskRead]
    attempts: list[AttemptRead]
    artifacts: list[ArtifactRead]
