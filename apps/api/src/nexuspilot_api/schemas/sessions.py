"""Conversation session and immutable message HTTP schemas."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from nexuspilot_api.models import MessageRole, SessionStatus
from nexuspilot_api.schemas.base import ApiModel


class SessionCreate(BaseModel):
    """Validate a durable conversation created for an active platform user."""

    model_config = ConfigDict(extra="forbid")

    user_id: str = Field(min_length=1, max_length=128)
    title: str | None = Field(default=None, min_length=1, max_length=255)


class SessionUpdate(BaseModel):
    """Allow callers to change only a conversation title or lifecycle status."""

    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=255)
    status: SessionStatus | None = None

    @model_validator(mode="after")
    def validate_non_empty_update(self) -> "SessionUpdate":
        """Reject a PATCH body that contains no mutable conversation field."""

        if self.title is None and self.status is None:
            raise ValueError("At least one session field must be provided")
        return self


class SessionRead(ApiModel):
    """Expose a conversation identity, owner, state, and timestamps."""

    session_id: str
    user_id: str
    title: str | None
    status: SessionStatus
    created_at: datetime
    updated_at: datetime


class MessageCreate(BaseModel):
    """Validate one immutable text or object-backed conversation message."""

    model_config = ConfigDict(extra="forbid")

    role: MessageRole
    content_type: str = Field(default="text", min_length=1, max_length=64)
    content_text: str | None = Field(default=None, min_length=1, max_length=16_000)
    content_uri: str | None = Field(default=None, min_length=1, max_length=512)
    run_id: str | None = Field(default=None, min_length=1, max_length=36)
    parent_message_id: str | None = Field(default=None, min_length=1, max_length=36)
    token_count: int | None = Field(default=None, ge=0)
    metadata_json: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_single_content_source(self) -> "MessageCreate":
        """Require exactly one inline-text or object-storage content source."""

        if (self.content_text is None) == (self.content_uri is None):
            raise ValueError("Exactly one of content_text or content_uri must be provided")
        return self


class MessageRead(ApiModel):
    """Expose immutable message content, ordering, lineage, and usage metadata."""

    message_id: str
    session_id: str
    run_id: str | None
    parent_message_id: str | None
    role: MessageRole
    content_type: str
    content_text: str | None
    content_uri: str | None
    sequence: int
    token_count: int | None
    metadata_json: dict[str, Any]
    created_at: datetime


class MessageSummary(ApiModel):
    """Expose bounded message metadata for history lists without full inline content."""

    message_id: str
    session_id: str
    run_id: str | None
    parent_message_id: str | None
    role: MessageRole
    content_type: str
    content_preview: str | None
    content_uri: str | None
    sequence: int
    token_count: int | None
    created_at: datetime
