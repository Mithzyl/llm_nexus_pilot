"""Public model exports and metadata registration for the NexusPilot API."""

from nexuspilot_api.models.attempts import LlmAttempt, LlmAttemptRetry
from nexuspilot_api.models.base import Base, TimestampMixin, new_id
from nexuspilot_api.models.enums import (
    AttemptStatus,
    MessageRole,
    RunStatus,
    SessionStatus,
    TaskStatus,
)
from nexuspilot_api.models.execution import LlmRun, LlmTask, LlmTaskDependency
from nexuspilot_api.models.identity import LlmMessage, LlmSession, User
from nexuspilot_api.models.records import (
    LlmArtifact,
    LlmEvaluation,
    LlmOutboxEvent,
    LlmToolCall,
)

__all__ = [
    "AttemptStatus",
    "Base",
    "LlmArtifact",
    "LlmAttempt",
    "LlmAttemptRetry",
    "LlmEvaluation",
    "LlmMessage",
    "LlmOutboxEvent",
    "LlmRun",
    "LlmSession",
    "LlmTask",
    "LlmTaskDependency",
    "LlmToolCall",
    "MessageRole",
    "RunStatus",
    "SessionStatus",
    "TaskStatus",
    "TimestampMixin",
    "User",
    "new_id",
]
