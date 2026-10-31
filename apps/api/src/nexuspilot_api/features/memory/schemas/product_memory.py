"""Public product memory operations, with no caller-supplied ownership fields."""

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from nexuspilot_api.features.memory.schemas.typed_memory import TypedMemory


class AppMemoryData(BaseModel):
    """Bound versioned App values without letting them override platform identity or permissions."""

    model_config = ConfigDict(extra="forbid")
    namespace: Literal["personal_assistant", "retail"]
    schema_version: Literal[1] = 1
    fields: dict[str, JsonValue]

    @model_validator(mode="after")
    def validate_fields(self):
        """Reject oversized/deep payloads and reserved platform fields at any nesting level."""
        if len(json.dumps(self.fields, ensure_ascii=False, allow_nan=False).encode()) > 8192:
            raise ValueError("App memory fields exceed 8192 bytes")
        reserved = {
            "user_id",
            "owner_user_id",
            "application_id",
            "external_user_id",
            "project_id",
            "scope",
            "status",
            "confirmed_sensitive",
            "enabled",
            "body_uri",
            "permissions",
        }
        pending = [(self.fields, 0)]
        while pending:
            value, depth = pending.pop()
            if depth > 6:
                raise ValueError("App memory fields are too deeply nested")
            if isinstance(value, dict):
                if any(key in reserved for key in value):
                    raise ValueError("App memory fields cannot override platform fields")
                pending.extend((item, depth + 1) for item in value.values())
            elif isinstance(value, list):
                pending.extend((item, depth + 1) for item in value)
        return self


class MemoryCommand(BaseModel):
    """Validate explicit user operations; model tools use a separate restricted schema."""

    model_config = ConfigDict(extra="forbid")
    action: Literal["save", "propose", "correct", "forget", "confirm", "disclose", "settings"]
    scope: Literal["personal", "project"] = "personal"
    key: str = Field(min_length=1, max_length=128)
    text: str | None = Field(default=None, min_length=1, max_length=4000)
    entry_id: str | None = Field(default=None, min_length=36, max_length=36)
    expected_version: int | None = Field(default=None, ge=0)
    enabled: bool | None = None
    sensitive: bool = False
    confirmed_sensitive: bool = False
    app_data: AppMemoryData | None = None
    typed_data: TypedMemory | None = None
    confirmed_type: bool | None = None

    @model_validator(mode="after")
    def fields_for_action(self):
        """Require action-specific targets and reject meaningless empty operations."""
        if self.action in {"save", "propose", "correct"} and (
            self.text is None or not self.text.strip()
        ):
            raise ValueError("Text is required")
        if self.action in {"correct", "forget", "confirm", "disclose"} and (
            not self.entry_id or self.expected_version is None or self.expected_version < 1
        ):
            raise ValueError("Target and current version are required")
        if self.action == "settings" and (self.expected_version is None or self.enabled is None):
            raise ValueError("Setting and current version are required")
        return self
