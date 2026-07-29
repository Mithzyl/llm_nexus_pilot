"""Task request and response schemas."""

from datetime import datetime

from pydantic import BaseModel, Field, model_validator

from nexuspilot_api.models import TaskStatus
from nexuspilot_api.schemas.base import ApiModel


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
