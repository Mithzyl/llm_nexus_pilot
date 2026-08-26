"""L2 Collaboration Memory HTTP schemas."""

import json
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from nexuspilot_api.models import HandoffStatus, MemoryPacketItemType, SessionMemoryStatus
from nexuspilot_api.schemas.base import ApiModel

HANDOFF_V1_KEYS = {
    "objective",
    "status",
    "confirmed_facts",
    "decisions",
    "files_read",
    "files_changed",
    "artifacts",
    "tests",
    "remaining_work",
    "risks",
    "unknowns",
    "invariants_for_next_agent",
}

RUN_MEMORY_STATE_V1_KEYS = {
    "objective",
    "confirmed_facts",
    "completed_work",
    "key_decisions",
    "changed_files",
    "artifacts",
    "tests",
    "remaining_work",
    "risks",
    "conflicts",
    "invariants",
}
HANDOFF_V1_LIST_KEYS = HANDOFF_V1_KEYS - {"objective", "status"}
HANDOFF_V1_HARD_CAP_TOKENS = 1_000

PACKET_V1_SECTIONS = {
    "role",
    "objective",
    "budget",
    "layers",
    "handoffs",
    "messages",
    "artifacts",
}


class AgentHandoffCreate(BaseModel):
    """Validate one immutable Handoff fact submitted by an Agent coordinator."""

    model_config = ConfigDict(extra="forbid")

    agent_run_id: str = Field(min_length=1, max_length=36)
    task_id: str | None = Field(default=None, min_length=1, max_length=36)
    schema_version: str = "agent_handoff.v1"
    status: HandoffStatus = HandoffStatus.COMPLETED
    handoff_json: dict
    supersedes_handoff_id: str | None = Field(default=None, min_length=1, max_length=36)
    idempotency_key: str = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def validate_handoff_contract(self) -> "AgentHandoffCreate":
        """Require the fixed v1 contract and completed-handoff completion gates."""

        if self.schema_version != "agent_handoff.v1":
            raise ValueError("Only agent_handoff.v1 is supported")
        missing_keys = HANDOFF_V1_KEYS - set(self.handoff_json)
        if missing_keys:
            raise ValueError(f"handoff_json is missing required keys: {sorted(missing_keys)}")
        unexpected_keys = set(self.handoff_json) - HANDOFF_V1_KEYS
        if unexpected_keys:
            raise ValueError(f"handoff_json contains unsupported keys: {sorted(unexpected_keys)}")
        invalid_list_fields = sorted(
            key for key in HANDOFF_V1_LIST_KEYS if not isinstance(self.handoff_json[key], list)
        )
        if invalid_list_fields:
            raise ValueError(f"handoff_json fields must be lists: {invalid_list_fields}")
        if self.handoff_json["status"] != self.status.value:
            raise ValueError("handoff_json status must match the Handoff status")
        if self.status == HandoffStatus.COMPLETED:
            if not str(self.handoff_json.get("objective") or "").strip():
                raise ValueError("Completed Handoff requires a non-empty objective")
        if self.status not in {HandoffStatus.COMPLETED, HandoffStatus.PARTIAL}:
            raise ValueError("New Handoff status must be completed or partial")
        serialized_handoff = json.dumps(
            self.handoff_json, ensure_ascii=False, separators=(",", ":")
        )
        if len(serialized_handoff.encode()) > HANDOFF_V1_HARD_CAP_TOKENS:
            raise ValueError("handoff_json exceeds the 1000-token hard cap")
        return self


class AgentHandoffRead(ApiModel):
    """Expose one immutable Handoff with its snapshot object evidence."""

    agent_handoff_id: str
    agent_run_id: str
    run_id: str
    task_id: str | None
    schema_version: str
    version: int
    status: HandoffStatus
    handoff_json: dict
    supersedes_handoff_id: str | None
    json_snapshot_object_id: str | None
    markdown_snapshot_object_id: str | None
    created_at: datetime


class RunMemoryRebuildCreate(BaseModel):
    """Validate one optimistic Run Memory Snapshot rebuild."""

    model_config = ConfigDict(extra="forbid")

    expected_previous_version: int = Field(default=0, ge=0)
    handoff_ids: list[str] = Field(default_factory=list, max_length=100)
    tokenizer_name: Literal["utf8_upper_bound"] = "utf8_upper_bound"
    tokenizer_version: Literal["utf8_bytes_upper_bound_v1"] = "utf8_bytes_upper_bound_v1"
    idempotency_key: str = Field(min_length=1, max_length=128)


class RunMemorySnapshotRead(ApiModel):
    """Expose one immutable merged Run Memory Snapshot version."""

    run_memory_snapshot_id: str
    run_id: str
    schema_version: str
    version: int
    status: SessionMemoryStatus
    state_json: dict
    previous_snapshot_id: str | None
    json_snapshot_object_id: str | None
    markdown_snapshot_object_id: str | None
    created_at: datetime
    activated_at: datetime | None
    failed_at: datetime | None
    error_code: str | None


class MemoryPacketBuildRequest(BaseModel):
    """Validate one immutable Memory Packet build from fixed current pointers."""

    model_config = ConfigDict(extra="forbid")

    user_id: str = Field(min_length=1, max_length=128)
    session_id: str | None = Field(default=None, min_length=1, max_length=36)
    project_id: str | None = Field(default=None, min_length=1, max_length=36)
    run_id: str | None = Field(default=None, min_length=1, max_length=36)
    task_id: str | None = Field(default=None, min_length=1, max_length=36)
    agent_run_id: str | None = Field(default=None, min_length=1, max_length=36)
    role: str | None = Field(default=None, min_length=1, max_length=64)
    include_user_profile: bool = True
    include_project_profile: bool = True
    include_session_memory: bool = True
    include_run_memory: bool = True
    include_working_state: bool = True
    include_handoffs: bool = True
    tokenizer_name: Literal["utf8_upper_bound"] = "utf8_upper_bound"
    tokenizer_version: Literal["utf8_bytes_upper_bound_v1"] = "utf8_bytes_upper_bound_v1"


class MemoryPacketItemRead(ApiModel):
    """Expose one frozen resource reference inside a Memory Packet."""

    item_type: MemoryPacketItemType
    resource_id: str
    resource_version: str | None
    item_order: int
    selected_token_count: int
    content_hash: str


class MemoryPacketRead(ApiModel):
    """Expose one immutable Memory Packet and its exact version-pinned item evidence."""

    memory_packet_id: str
    agent_run_id: str | None
    run_id: str | None
    task_id: str | None
    user_id: str
    schema_version: str
    packet_json: dict
    estimated_token_count: int
    tokenizer_name: str
    tokenizer_version: str
    json_snapshot_object_id: str | None
    created_at: datetime
    items: list[MemoryPacketItemRead]
