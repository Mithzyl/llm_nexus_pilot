"""L1 Session State and Session Summary HTTP schemas."""

import json
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from nexuspilot_api.models import SessionMemoryStatus
from nexuspilot_api.schemas.base import ApiModel

SESSION_STATE_V1_KEYS = {
    "goal",
    "constraints",
    "decisions",
    "open_questions",
    "active_entities",
}
SESSION_SUMMARY_V1_KEYS = {
    "coverage",
    "confirmed_facts",
    "decisions",
    "open_items",
    "artifacts",
}
SESSION_STATE_V1_LIST_KEYS = SESSION_STATE_V1_KEYS - {"goal"}
SESSION_SUMMARY_V1_LIST_KEYS = SESSION_SUMMARY_V1_KEYS - {"coverage"}
SESSION_MEMORY_HARD_CAP_TOKENS = 1_500


class SessionStateCreate(BaseModel):
    """Validate one versioned Session State snapshot with optimistic concurrency."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "session_state.v1"
    state_json: dict
    state_through_message_sequence: int | None = Field(default=None, ge=1)
    state_through_message_id: str | None = Field(default=None, min_length=1, max_length=36)
    expected_previous_version: int = Field(default=0, ge=0)
    generation_attempt_id: str | None = Field(default=None, min_length=1, max_length=36)
    idempotency_key: str = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def validate_state_schema(self) -> "SessionStateCreate":
        """Reject state documents that do not satisfy the fixed v1 contract."""

        if self.schema_version != "session_state.v1":
            raise ValueError("Only session_state.v1 is supported")
        missing_keys = SESSION_STATE_V1_KEYS - set(self.state_json)
        if missing_keys:
            raise ValueError(f"state_json is missing required keys: {sorted(missing_keys)}")
        unexpected_keys = set(self.state_json) - SESSION_STATE_V1_KEYS
        if unexpected_keys:
            raise ValueError(f"state_json contains unsupported keys: {sorted(unexpected_keys)}")
        if not isinstance(self.state_json["goal"], str):
            raise ValueError("state_json goal must be a string")
        invalid_list_fields = sorted(
            key for key in SESSION_STATE_V1_LIST_KEYS if not isinstance(self.state_json[key], list)
        )
        if invalid_list_fields:
            raise ValueError(f"state_json fields must be lists: {invalid_list_fields}")
        if _json_token_upper_bound(self.state_json) > SESSION_MEMORY_HARD_CAP_TOKENS:
            raise ValueError("state_json exceeds the 1500-token hard cap")
        if self.state_through_message_id and not self.state_through_message_sequence:
            raise ValueError(
                "state_through_message_sequence is required with state_through_message_id"
            )
        return self


class SessionSummaryCreate(BaseModel):
    """Validate one versioned Session Summary over an explicit message range."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "session_summary.v1"
    summary_json: dict
    summary_from_message_sequence: int = Field(ge=1)
    summary_through_message_sequence: int = Field(ge=1)
    summary_through_message_id: str | None = Field(default=None, min_length=1, max_length=36)
    model: str = Field(min_length=1, max_length=128)
    generation_attempt_id: str | None = Field(default=None, min_length=1, max_length=36)
    expected_previous_version: int = Field(default=0, ge=0)
    idempotency_key: str = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def validate_summary_contract(self) -> "SessionSummaryCreate":
        """Enforce monotonic message ranges and the fixed summary schema."""

        if self.schema_version != "session_summary.v1":
            raise ValueError("Only session_summary.v1 is supported")
        if self.summary_from_message_sequence > self.summary_through_message_sequence:
            raise ValueError("Summary range must be non-empty and monotonic")
        missing_keys = SESSION_SUMMARY_V1_KEYS - set(self.summary_json)
        if missing_keys:
            raise ValueError(f"summary_json is missing required keys: {sorted(missing_keys)}")
        unexpected_keys = set(self.summary_json) - SESSION_SUMMARY_V1_KEYS
        if unexpected_keys:
            raise ValueError(f"summary_json contains unsupported keys: {sorted(unexpected_keys)}")
        if not isinstance(self.summary_json["coverage"], str):
            raise ValueError("summary_json coverage must be a string")
        invalid_list_fields = sorted(
            key
            for key in SESSION_SUMMARY_V1_LIST_KEYS
            if not isinstance(self.summary_json[key], list)
        )
        if invalid_list_fields:
            raise ValueError(f"summary_json fields must be lists: {invalid_list_fields}")
        if _json_token_upper_bound(self.summary_json) > SESSION_MEMORY_HARD_CAP_TOKENS:
            raise ValueError("summary_json exceeds the 1500-token hard cap")
        return self


def _json_token_upper_bound(content: dict) -> int:
    """Estimate structured content with the supported one-token-per-UTF-8-byte bound."""

    serialized = json.dumps(content, ensure_ascii=False, separators=(",", ":"))
    return max(1, len(serialized.encode()))


class SessionStateRead(ApiModel):
    """Expose one immutable Session State version."""

    session_state_id: str
    session_id: str
    schema_version: str
    version: int
    status: SessionMemoryStatus
    state_json: dict
    state_through_message_id: str | None
    state_through_message_sequence: int | None
    generation_attempt_id: str | None
    json_snapshot_object_id: str | None
    markdown_snapshot_object_id: str | None
    created_at: datetime
    activated_at: datetime | None
    failed_at: datetime | None
    error_code: str | None


class SessionSummaryRead(ApiModel):
    """Expose one immutable Session Summary version."""

    session_summary_id: str
    session_id: str
    schema_version: str
    version: int
    status: SessionMemoryStatus
    summary_json: dict
    summary_from_message_sequence: int | None
    summary_through_message_id: str | None
    summary_through_message_sequence: int | None
    generation_attempt_id: str | None
    json_snapshot_object_id: str | None
    markdown_snapshot_object_id: str | None
    created_at: datetime
    activated_at: datetime | None
    failed_at: datetime | None
    error_code: str | None


class SessionMemoryView(ApiModel):
    """Expose the current Session State, Summary, and their coverage evidence."""

    session_id: str
    user_id: str
    current_state: SessionStateRead | None
    current_summary: SessionSummaryRead | None
    is_stale: bool = False
