"""Public model reasoning block and replay-event API schemas."""

from datetime import datetime
from typing import Any

from nexuspilot_models.contracts import ReasoningBlockStatus, ReasoningPresentationKind
from pydantic import BaseModel


class ReasoningBlockRead(BaseModel):
    """Expose one authoritative reasoning presentation without provider-private state."""

    block_id: str
    response_id: str
    kind: ReasoningPresentationKind
    status: ReasoningBlockStatus
    text: str | None
    reasoning_tokens: int | None
    started_at: datetime
    first_visible_token_at: datetime | None
    completed_at: datetime | None
    final_event_sequence: int | None


class ModelResponseEventRead(BaseModel):
    """Expose one committed sanitized event using the versioned stream envelope."""

    schema_version: str
    event_id: str
    response_id: str
    run_id: str
    turn_id: str | None
    step_id: str | None
    sequence: int
    timestamp_ms: int
    type: str
    data: dict[str, Any]


class ModelResponseEventPage(BaseModel):
    """Return a bounded ordered replay page and its next sequence position."""

    items: list[ModelResponseEventRead]
    next_after_sequence: int | None
    has_more: bool
    limit: int
