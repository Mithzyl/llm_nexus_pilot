"""Memory Store and deterministic retrieval HTTP schemas."""

from datetime import UTC, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from nexuspilot_api.models import (
    ApprovalMethod,
    MemoryCreatedByType,
    MemoryMutationActorType,
    MemoryMutationOperation,
    MemorySourceType,
    MemoryStatus,
    MemoryTrustLevel,
    MemoryType,
    SensitivityClassification,
)
from nexuspilot_api.schemas.base import ApiModel


class MemorySourceCreate(BaseModel):
    """Identify one evidence resource that the Memory service must validate."""

    model_config = ConfigDict(extra="forbid")

    source_type: MemorySourceType
    source_resource_id: str = Field(min_length=1, max_length=128)
    trust_level: MemoryTrustLevel | None = None


class MemorySourceRead(ApiModel):
    """Expose a Memory version's stable source locator and evidence hash."""

    memory_source_id: str
    source_type: MemorySourceType
    source_resource_id: str
    source_content_hash: str
    trust_level: MemoryTrustLevel
    source_order: int


class MemoryCreate(BaseModel):
    """Validate one sourced Memory proposal within an explicit user scope."""

    model_config = ConfigDict(extra="forbid")

    user_id: str = Field(min_length=1, max_length=128)
    session_id: str | None = Field(default=None, min_length=1, max_length=36)
    run_id: str | None = Field(default=None, min_length=1, max_length=36)
    task_id: str | None = Field(default=None, min_length=1, max_length=36)
    project_id: str | None = Field(default=None, min_length=1, max_length=36)
    memory_type: MemoryType
    content_text: str = Field(min_length=1, max_length=4_000)
    importance: Decimal = Field(default=Decimal("0.50"), ge=0, le=1)
    confidence: Decimal = Field(default=Decimal("0.50"), ge=0, le=1)
    status: MemoryStatus = MemoryStatus.CANDIDATE
    semantic_key: str | None = Field(default=None, min_length=1, max_length=255)
    supersedes_memory_id: str | None = Field(default=None, min_length=1, max_length=36)
    expires_at: datetime | None = None
    idempotency_key: str = Field(min_length=1, max_length=128)
    created_by_type: MemoryCreatedByType = MemoryCreatedByType.TRUSTED_CALLER
    created_by_attempt_id: str | None = Field(default=None, min_length=1, max_length=36)
    sensitivity_classification: SensitivityClassification = SensitivityClassification.NONE
    is_core_profile_eligible: bool = False
    sources: list[MemorySourceCreate] = Field(min_length=1, max_length=20)

    @field_validator("content_text")
    @classmethod
    def validate_non_blank_content(cls, value: str) -> str:
        """Reject whitespace-only Memory content while preserving meaningful formatting."""

        if not value.strip():
            raise ValueError("content_text must contain non-whitespace text")
        return value

    @field_validator("expires_at")
    @classmethod
    def validate_timezone_aware_expiration(cls, value: datetime | None) -> datetime | None:
        """Reject ambiguous expiration timestamps and normalize valid instants to UTC."""

        if value is not None and value.tzinfo is None:
            raise ValueError("expires_at must include a timezone")
        return value.astimezone(UTC) if value is not None else None

    @model_validator(mode="after")
    def validate_creation_actor_and_state(self) -> "MemoryCreate":
        """Restrict creation to candidate or active facts and keep model output untrusted."""

        if self.status not in {MemoryStatus.CANDIDATE, MemoryStatus.ACTIVE}:
            raise ValueError("New Memory status must be candidate or active")
        if self.created_by_type == MemoryCreatedByType.MODEL_ATTEMPT:
            if self.created_by_attempt_id is None:
                raise ValueError("created_by_attempt_id is required for model-created Memory")
            if self.status != MemoryStatus.CANDIDATE:
                raise ValueError("Model-created Memory must start as candidate")
        elif self.created_by_attempt_id is not None:
            raise ValueError("created_by_attempt_id is only valid for model-created Memory")
        if self.supersedes_memory_id is not None and self.status != MemoryStatus.ACTIVE:
            raise ValueError("Only an active Memory can supersede another Memory")
        if self.supersedes_memory_id is not None and self.semantic_key is None:
            raise ValueError("supersedes_memory_id requires semantic_key")
        return self


class MemoryUpdate(BaseModel):
    """Validate one optimistic Memory correction or lifecycle transition."""

    model_config = ConfigDict(extra="forbid")

    expected_version_number: int = Field(ge=1)
    idempotency_key: str = Field(min_length=1, max_length=128)
    content_text: str | None = Field(default=None, min_length=1, max_length=4_000)
    importance: Decimal | None = Field(default=None, ge=0, le=1)
    confidence: Decimal | None = Field(default=None, ge=0, le=1)
    status: MemoryStatus | None = None
    semantic_key: str | None = Field(default=None, min_length=1, max_length=255)
    supersedes_memory_id: str | None = Field(default=None, min_length=1, max_length=36)
    expires_at: datetime | None = None
    sources: list[MemorySourceCreate] | None = Field(default=None, min_length=1, max_length=20)

    @field_validator("content_text")
    @classmethod
    def validate_non_blank_content(cls, value: str | None) -> str | None:
        """Reject whitespace-only corrections while leaving omitted content unchanged."""

        if value is not None and not value.strip():
            raise ValueError("content_text must contain non-whitespace text")
        return value

    @field_validator("expires_at")
    @classmethod
    def validate_timezone_aware_expiration(cls, value: datetime | None) -> datetime | None:
        """Reject ambiguous update timestamps and normalize valid instants to UTC."""

        if value is not None and value.tzinfo is None:
            raise ValueError("expires_at must include a timezone")
        return value.astimezone(UTC) if value is not None else None

    @model_validator(mode="after")
    def validate_memory_update_fields(self) -> "MemoryUpdate":
        """Require a meaningful mutation and evidence for each new content version."""

        mutable_fields = {
            "content_text",
            "importance",
            "confidence",
            "status",
            "semantic_key",
            "supersedes_memory_id",
            "expires_at",
        }
        if not (self.model_fields_set & mutable_fields):
            raise ValueError("At least one Memory field must be provided")
        non_nullable_fields = {
            "content_text",
            "importance",
            "confidence",
            "status",
            "supersedes_memory_id",
        }
        for field_name in self.model_fields_set & non_nullable_fields:
            if getattr(self, field_name) is None:
                raise ValueError(f"{field_name} must not be null when provided")
        creates_version = bool(self.model_fields_set & {"content_text", "importance", "confidence"})
        if creates_version and not self.sources:
            raise ValueError("Memory corrections require at least one source")
        if not creates_version and self.sources is not None:
            raise ValueError("sources are only valid when creating a new Memory version")
        if self.status in {MemoryStatus.DELETED, MemoryStatus.SUPERSEDED}:
            raise ValueError("Use the dedicated delete or replacement behavior")
        if self.supersedes_memory_id is not None and self.status != MemoryStatus.ACTIVE:
            raise ValueError("supersedes_memory_id requires activation")
        if self.supersedes_memory_id is not None and self.semantic_key is None:
            raise ValueError("supersedes_memory_id requires semantic_key")
        return self


class MemoryVersionRead(ApiModel):
    """Expose one immutable Memory content revision and its source evidence."""

    memory_version_id: str
    memory_id: str
    version_number: int
    content_text: str | None
    content_hash: str
    importance: Decimal
    confidence: Decimal
    estimated_token_count: int
    token_estimator_version: str
    created_by_type: MemoryCreatedByType
    created_by_attempt_id: str | None
    content_erased_at: datetime | None
    created_at: datetime
    sources: list[MemorySourceRead] = Field(default_factory=list)


class MemoryRead(ApiModel):
    """Expose one stable Memory with its current immutable content version."""

    memory_id: str
    user_id: str
    session_id: str | None
    run_id: str | None
    task_id: str | None
    project_id: str | None
    memory_type: MemoryType
    status: MemoryStatus
    version_number: int
    content_text: str | None
    content_hash: str
    importance: Decimal
    confidence: Decimal
    estimated_token_count: int
    semantic_key: str | None
    supersedes_memory_id: str | None
    approval_method: ApprovalMethod
    approved_at: datetime | None
    sensitivity_classification: SensitivityClassification
    is_core_profile_eligible: bool
    expires_at: datetime | None
    deleted_at: datetime | None
    sources: list[MemorySourceRead] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class MemoryMutationRead(ApiModel):
    """Expose one audited Memory mutation with before/after state evidence."""

    memory_mutation_id: str
    memory_id: str
    idempotency_key: str
    request_hash: str
    operation: MemoryMutationOperation
    actor_type: MemoryMutationActorType
    actor_id: str | None
    before_version_number: int | None
    after_version_number: int
    before_status: MemoryStatus | None
    after_status: MemoryStatus
    change_json: dict | None
    created_at: datetime


class MemorySummary(ApiModel):
    """Expose bounded Memory metadata without returning complete version history."""

    memory_id: str
    user_id: str
    session_id: str | None
    run_id: str | None
    task_id: str | None
    project_id: str | None
    memory_type: MemoryType
    status: MemoryStatus
    version_number: int
    content_preview: str | None
    importance: Decimal
    confidence: Decimal
    semantic_key: str | None
    expires_at: datetime | None
    deleted_at: datetime | None
    created_at: datetime
    updated_at: datetime


class MemoryRetrievalCreate(BaseModel):
    """Validate one owner-scoped, bounded, deterministic Memory retrieval."""

    model_config = ConfigDict(extra="forbid")

    user_id: str = Field(min_length=1, max_length=128)
    session_id: str | None = Field(default=None, min_length=1, max_length=36)
    run_id: str | None = Field(default=None, min_length=1, max_length=36)
    task_id: str | None = Field(default=None, min_length=1, max_length=36)
    query_text: str = Field(min_length=1, max_length=2_000)
    memory_types: list[MemoryType] = Field(default_factory=list, max_length=5)
    limit: int = Field(default=20, ge=1, le=100)
    token_budget: int = Field(default=2_000, ge=1, le=20_000)
    idempotency_key: str = Field(min_length=1, max_length=128)

    @field_validator("memory_types")
    @classmethod
    def validate_unique_memory_types(cls, value: list[MemoryType]) -> list[MemoryType]:
        """Reject duplicate type filters that would produce ambiguous request hashes."""

        if len(set(value)) != len(value):
            raise ValueError("memory_types must not contain duplicates")
        return value


class MemoryRetrievalResultRead(ApiModel):
    """Expose one ranked candidate with deterministic score and budget evidence."""

    rank: int
    memory_id: str
    memory_version_id: str
    memory_type: MemoryType
    content_text: str | None
    total_score: int
    lexical_score: int
    scope_score: int
    importance_score: int
    confidence_score: int
    recency_score: int
    estimated_token_count: int
    selected: bool
    exclusion_reason: str | None


class MemoryRetrievalRead(ApiModel):
    """Expose a persisted Memory retrieval and its complete bounded result evidence."""

    memory_retrieval_id: str
    user_id: str
    session_id: str | None
    run_id: str | None
    task_id: str | None
    query_text: str
    query_hash: str
    memory_types: list[MemoryType]
    limit: int
    token_budget: int
    candidate_method: str
    ranker_version: str
    idempotency_key: str
    created_at: datetime
    completed_at: datetime
    results: list[MemoryRetrievalResultRead]
