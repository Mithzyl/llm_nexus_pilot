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


class MemoryStatus(str, enum.Enum):
    """Lifecycle states for one durable Memory fact."""

    CANDIDATE = "candidate"
    ACTIVE = "active"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"
    DELETED = "deleted"


class MemoryType(str, enum.Enum):
    """Supported meanings and retention scopes for durable Memory facts."""

    WORKING_CONTEXT = "working_context"
    SESSION_EPISODE = "session_episode"
    USER_FACT = "user_fact"
    USER_PREFERENCE = "user_preference"
    EXECUTION_LESSON = "execution_lesson"
    PROJECT_FACT = "project_fact"
    PROJECT_DECISION = "project_decision"
    PROJECT_RULE = "project_rule"
    PROJECT_CONVENTION = "project_convention"
    PROJECT_ENVIRONMENT = "project_environment"


class MemoryTrustLevel(str, enum.Enum):
    """Fixed trust boundaries assigned by the receiving edge, never self-reported."""

    DIRECT_USER_STATEMENT = "direct_user_statement"
    USER_CONFIRMED = "user_confirmed"
    MODEL_INFERENCE = "model_inference"
    INTERNAL_SYSTEM_RESULT = "internal_system_result"
    EXTERNAL_UNTRUSTED = "external_untrusted"


class ApprovalMethod(str, enum.Enum):
    """Documented approval path that promoted a candidate to an active fact."""

    NONE = "none"
    TRUSTED_CALLER = "trusted_caller"
    POLICY = "policy"
    REVIEWER = "reviewer"


class SensitivityClassification(str, enum.Enum):
    """Deployment-policy-driven sensitivity label for durable facts."""

    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class MemoryMutationOperation(str, enum.Enum):
    """Audited operations recorded in the immutable mutation ledger."""

    CREATE = "create"
    CORRECT = "correct"
    ACTIVATE = "activate"
    REJECT = "reject"
    SUPERSEDE = "supersede"
    DELETE = "delete"
    UPDATE_METADATA = "update_metadata"


class MemoryMutationActorType(str, enum.Enum):
    """Actor categories that can append Memory mutations."""

    TRUSTED_CALLER = "trusted_caller"
    MODEL_ATTEMPT = "model_attempt"
    SYSTEM = "system"


class AgentRunStatus(str, enum.Enum):
    """Lifecycle states for one role-owned execution of a task."""

    PENDING = "pending"
    RUNNING = "running"
    WAITING = "waiting"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AgentTurnType(str, enum.Enum):
    """Kinds of model/tool loop steps inside an Agent Run."""

    MODEL = "model"
    TOOL = "tool"


class AgentTurnStatus(str, enum.Enum):
    """Completion states for one Agent Turn."""

    STARTED = "started"
    COMPLETED = "completed"
    FAILED = "failed"


class AgentWorkingStateStatus(str, enum.Enum):
    """Version states for one Agent Working Memory checkpoint."""

    ACTIVE = "active"
    SUPERSEDED = "superseded"
    FINALIZED = "finalized"
    ERASED = "erased"


class SessionMemoryStatus(str, enum.Enum):
    """Lifecycle states for Session State and Session Summary versions."""

    GENERATING = "generating"
    ACTIVE = "active"
    FAILED = "failed"
    SUPERSEDED = "superseded"


class HandoffStatus(str, enum.Enum):
    """Immutable completion evidence states for an Agent Handoff."""

    COMPLETED = "completed"
    PARTIAL = "partial"
    CORRECTED = "corrected"
    SUPERSEDED = "superseded"


class MemoryPacketItemType(str, enum.Enum):
    """Typed resource references frozen inside one Memory Packet."""

    AGENT_WORKING_STATE = "agent_working_state"
    SESSION_STATE = "session_state"
    SESSION_SUMMARY = "session_summary"
    RUN_MEMORY_SNAPSHOT = "run_memory_snapshot"
    USER_MEMORY_PROFILE = "user_memory_profile"
    PROJECT_MEMORY_PROFILE = "project_memory_profile"
    HANDOFF = "handoff"
    MEMORY = "memory"
    MESSAGE = "message"
    ARTIFACT = "artifact"


class ProjectStatus(str, enum.Enum):
    """Lifecycle states for the minimal Project scope entity."""

    ACTIVE = "active"
    ARCHIVED = "archived"


class ProjectMemoryStatus(str, enum.Enum):
    """Explicit opt-in states for Project Memory formation and injection."""

    DISABLED = "disabled"
    ENABLED = "enabled"
    SUSPENDED = "suspended"


class KnowledgeDocumentStatus(str, enum.Enum):
    """Lifecycle states for one Knowledge document identity."""

    PENDING = "pending"
    READY = "ready"
    INACTIVE = "inactive"


class KnowledgeVersionStatus(str, enum.Enum):
    """Processing states for one Knowledge document version."""

    PENDING = "pending"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"
    INACTIVE = "inactive"


class PromptTemplateStatus(str, enum.Enum):
    """Enablement states for one Prompt template identity."""

    ENABLED = "enabled"
    DISABLED = "disabled"


class ModelCatalogStatus(str, enum.Enum):
    """Enablement states for one immutable model capability snapshot."""

    ENABLED = "enabled"
    DISABLED = "disabled"


class ContextBuildStatus(str, enum.Enum):
    """Completion states for one persisted Context Build."""

    COMPLETED = "completed"
    FAILED = "failed"


class ContextSourceSelectionStatus(str, enum.Enum):
    """Selection evidence states for one Context source."""

    SELECTED = "selected"
    EXCLUDED = "excluded"


class EvaluationStatus(str, enum.Enum):
    """Execution states for one Evaluation independent of its verdict."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class EvaluationVerdict(str, enum.Enum):
    """Rule conclusions kept separate from execution status."""

    PASS = "pass"
    FAIL = "fail"
    UNKNOWN = "unknown"


class EvaluationRuleSetStatus(str, enum.Enum):
    """Enablement states for one Evaluation rule set version."""

    ENABLED = "enabled"
    DISABLED = "disabled"


class LessonCandidateStatus(str, enum.Enum):
    """Review lifecycle for a lesson extracted from an execution."""

    CANDIDATE = "candidate"
    VERIFIED = "verified"
    PROMOTED = "promoted"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


class SnapshotObjectStatus(str, enum.Enum):
    """Registry states for immutable MinIO snapshot objects."""

    ACTIVE = "active"
    DELETED = "deleted"
    ORPHAN = "orphan"


class MemorySourceType(str, enum.Enum):
    """Persisted resource kinds that can provide evidence for a Memory version."""

    MESSAGE = "message"
    MODEL_ATTEMPT = "model_attempt"
    ARTIFACT = "artifact"
    TOOL_CALL = "tool_call"
    TRUSTED_REQUEST = "trusted_request"


class MemoryCreatedByType(str, enum.Enum):
    """Actors allowed to propose a Memory version."""

    TRUSTED_CALLER = "trusted_caller"
    MODEL_ATTEMPT = "model_attempt"
    SYSTEM = "system"


class MemoryRetrievalStatus(str, enum.Enum):
    """Execution states for an auditable Memory retrieval."""

    COMPLETED = "completed"
