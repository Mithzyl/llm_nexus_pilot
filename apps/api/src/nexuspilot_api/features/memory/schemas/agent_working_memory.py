"""L0 Agent Working Memory and Agent Run HTTP schemas."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from nexuspilot_api.models import AgentRunStatus, AgentWorkingStateStatus
from nexuspilot_api.schemas.base import ApiModel

AGENT_WORKING_STATE_V1_KEYS = {
    "current_objective",
    "searched_queries",
    "files_read",
    "rejected_hypotheses",
    "next_actions",
    "pending_tool_call_ids",
    "remaining_budget",
    "tentative_findings",
    "blocked_on",
}


class AgentRunCreate(BaseModel):
    """Validate creation of one role-owned execution of a Task."""

    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(min_length=1, max_length=36)
    task_id: str = Field(min_length=1, max_length=36)
    agent_role: str = Field(min_length=1, max_length=64)
    provider: str | None = Field(default=None, min_length=1, max_length=64)
    model: str | None = Field(default=None, min_length=1, max_length=128)


class AgentWorkingStateCreate(BaseModel):
    """Validate one immutable L0 check point with optimistic concurrency."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "agent_working_state.v1"
    state_json: dict
    agent_turn_id: str | None = Field(default=None, min_length=1, max_length=36)
    expected_previous_version: int = Field(default=0, ge=0)
    expires_at: datetime | None = None
    tokenizer_name: Literal["utf8_upper_bound"] = "utf8_upper_bound"
    tokenizer_version: Literal["utf8_bytes_upper_bound_v1"] = "utf8_bytes_upper_bound_v1"
    idempotency_key: str = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def validate_working_state_schema(self) -> "AgentWorkingStateCreate":
        """Reject checkpoints that are missing required recovery state or hide reasoning."""

        if self.schema_version != "agent_working_state.v1":
            raise ValueError("Only agent_working_state.v1 is supported")
        missing_keys = AGENT_WORKING_STATE_V1_KEYS - set(self.state_json)
        if missing_keys:
            raise ValueError(f"state_json is missing required keys: {sorted(missing_keys)}")
        unexpected_keys = set(self.state_json) - AGENT_WORKING_STATE_V1_KEYS
        if unexpected_keys:
            raise ValueError(f"state_json contains unsupported keys: {sorted(unexpected_keys)}")
        return self


class AgentWorkingStateRead(ApiModel):
    """Expose one immutable Agent Working State version."""

    agent_working_state_version_id: str
    agent_run_id: str
    agent_turn_id: str | None
    version: int
    status: AgentWorkingStateStatus
    state_json: dict | None
    estimated_token_count: int
    tokenizer_name: str
    tokenizer_version: str
    expires_at: datetime | None
    content_erased_at: datetime | None
    created_at: datetime


class AgentRunRead(ApiModel):
    """Expose one Agent Run and its current recovery pointer."""

    agent_run_id: str
    run_id: str
    task_id: str
    agent_role: str
    status: AgentRunStatus
    provider: str | None
    model: str | None
    current_working_state_version_id: str | None
    current_turn_sequence: int
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
