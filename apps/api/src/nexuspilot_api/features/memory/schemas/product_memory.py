"""Public product memory operations, with no caller-supplied ownership fields."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


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
