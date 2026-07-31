"""Public model exports and metadata registration for the NexusPilot API."""

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
from nexuspilot_api.models.model_attempts import (
    LlmModelAttempt,
    LlmModelTransportAttempt,
)
from nexuspilot_api.models.records import (
    LlmModelToolCall,
    LlmOutboxEvent,
    LlmRunArtifact,
    LlmTaskEvaluation,
)

__all__ = [
    "AttemptStatus",
    "Base",
    "LlmModelAttempt",
    "LlmModelTransportAttempt",
    "LlmModelToolCall",
    "LlmMessage",
    "LlmOutboxEvent",
    "LlmRun",
    "LlmRunArtifact",
    "LlmSession",
    "LlmTask",
    "LlmTaskDependency",
    "LlmTaskEvaluation",
    "MessageRole",
    "RunStatus",
    "SessionStatus",
    "TaskStatus",
    "TimestampMixin",
    "User",
    "new_id",
]
