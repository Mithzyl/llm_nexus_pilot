"""Run request, summary, and detail schemas."""

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from nexuspilot_api.models import RunStatus
from nexuspilot_api.schemas.base import ApiModel
from nexuspilot_api.schemas.model_attempts import ModelAttemptRead
from nexuspilot_api.schemas.run_artifacts import RunArtifactRead
from nexuspilot_api.schemas.tasks import TaskRead


class RunCreate(BaseModel):
    """Validate the user request used to create a run."""

    user_request: str = Field(min_length=1, max_length=100_000)
    user_id: str = Field(min_length=1, max_length=128)
    session_id: str | None = Field(default=None, max_length=36)
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


class RunSummary(ApiModel):
    """Expose bounded run-list metadata without the complete user request or children."""

    run_id: str
    user_id: str
    session_id: str | None
    request_preview: str
    run_type: str
    status: RunStatus
    budget_limit: Decimal | None
    cost_used: Decimal
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class RunDetail(RunRead):
    """Expose a bounded compatibility snapshot and indicate truncated child histories."""

    tasks: list[TaskRead]
    attempts: list[ModelAttemptRead]
    artifacts: list[RunArtifactRead]
    tasks_has_more: bool = False
    attempts_has_more: bool = False
    artifacts_has_more: bool = False
