"""Lifecycle enums persisted by the execution models."""

import enum


class RunStatus(str, enum.Enum):
    """Lifecycle states for an end-to-end user request."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLED = "cancelled"


class TaskStatus(str, enum.Enum):
    """Lifecycle states reserved for synchronous and future asynchronous execution."""

    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    WAITING_FOR_INPUT = "waiting_for_input"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    WAITING_FOR_DEPENDENCY = "waiting_for_dependency"
    RETRY_SCHEDULED = "retry_scheduled"
    CANCEL_REQUESTED = "cancel_requested"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AttemptStatus(str, enum.Enum):
    """Completion states for one logical provider request."""

    STARTED = "started"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"


class SessionStatus(str, enum.Enum):
    """Lifecycle states for a persisted LLM conversation session."""

    ACTIVE = "active"
    ARCHIVED = "archived"


class MessageRole(str, enum.Enum):
    """Supported roles for persisted LLM conversation messages."""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"
