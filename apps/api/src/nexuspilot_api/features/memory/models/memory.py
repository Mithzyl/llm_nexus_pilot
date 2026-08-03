"""Durable Memory facts, immutable versions, evidence, and retrieval records."""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from nexuspilot_api.models.base import Base, TimestampMixin, new_id, utc_now
from nexuspilot_api.models.enums import (
    ApprovalMethod,
    MemoryCreatedByType,
    MemoryMutationActorType,
    MemoryMutationOperation,
    MemoryRetrievalStatus,
    MemorySourceType,
    MemoryStatus,
    MemoryTrustLevel,
    MemoryType,
    SensitivityClassification,
)


class LlmMemory(TimestampMixin, Base):
    """Represent one stable Memory identity whose content evolves through versions."""

    __tablename__ = "llm_memories"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "creation_idempotency_key",
            name="uq_memory_user_creation_key",
        ),
        UniqueConstraint(
            "active_semantic_key_hash",
            name="uq_memory_active_semantic_key_hash",
        ),
        Index(
            "ix_memory_user_status_created_id",
            "user_id",
            "status",
            "created_at",
            "memory_id",
        ),
        Index(
            "ix_memory_session_status_created_id",
            "session_id",
            "status",
            "created_at",
            "memory_id",
        ),
        Index(
            "ix_memory_run_status_created_id",
            "run_id",
            "status",
            "created_at",
            "memory_id",
        ),
        Index(
            "ix_memory_task_status_created_id",
            "task_id",
            "status",
            "created_at",
            "memory_id",
        ),
    )

    memory_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.user_id", ondelete="CASCADE"),
        index=True,
    )
    session_id: Mapped[str | None] = mapped_column(
        ForeignKey("llm_sessions.session_id", ondelete="RESTRICT"),
        index=True,
    )
    run_id: Mapped[str | None] = mapped_column(
        ForeignKey("llm_runs.run_id", ondelete="RESTRICT"),
        index=True,
    )
    task_id: Mapped[str | None] = mapped_column(
        ForeignKey("llm_tasks.task_id", ondelete="RESTRICT"),
        index=True,
    )
    project_id: Mapped[str | None] = mapped_column(
        ForeignKey("llm_projects.project_id", ondelete="RESTRICT"),
        index=True,
    )
    memory_type: Mapped[MemoryType] = mapped_column(
        Enum(MemoryType, native_enum=False, length=32),
        index=True,
    )
    status: Mapped[MemoryStatus] = mapped_column(
        Enum(MemoryStatus, native_enum=False, length=32),
        index=True,
    )
    current_version_number: Mapped[int] = mapped_column(Integer, default=1)
    semantic_key: Mapped[str | None] = mapped_column(String(255))
    active_semantic_key_hash: Mapped[str | None] = mapped_column(String(64))
    supersedes_memory_id: Mapped[str | None] = mapped_column(
        ForeignKey("llm_memories.memory_id", ondelete="SET NULL"),
        index=True,
    )
    approval_method: Mapped[ApprovalMethod] = mapped_column(
        Enum(ApprovalMethod, native_enum=False, length=32),
        default=ApprovalMethod.NONE,
        index=True,
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_by_actor_id: Mapped[str | None] = mapped_column(String(128))
    sensitivity_classification: Mapped[SensitivityClassification] = mapped_column(
        Enum(SensitivityClassification, native_enum=False, length=32),
        default=SensitivityClassification.NONE,
        index=True,
    )
    is_core_profile_eligible: Mapped[bool] = mapped_column(
        Boolean, default=False, index=True
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    creation_idempotency_key: Mapped[str] = mapped_column(String(128))
    creation_request_hash: Mapped[str] = mapped_column(String(64))


class LlmMemoryVersion(Base):
    """Store one immutable content revision for a stable Memory identity."""

    __tablename__ = "llm_memory_versions"
    __table_args__ = (
        UniqueConstraint(
            "memory_id",
            "version_number",
            name="uq_memory_version_number",
        ),
        CheckConstraint(
            "importance >= 0 AND importance <= 1",
            name="ck_memory_version_importance",
        ),
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_memory_version_confidence",
        ),
    )

    memory_version_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    memory_id: Mapped[str] = mapped_column(
        ForeignKey("llm_memories.memory_id", ondelete="CASCADE"),
        index=True,
    )
    version_number: Mapped[int] = mapped_column(Integer)
    content_text: Mapped[str | None] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    normalized_content_hash: Mapped[str] = mapped_column(String(64), index=True)
    importance: Mapped[Decimal] = mapped_column(Numeric(5, 4))
    confidence: Mapped[Decimal] = mapped_column(Numeric(5, 4))
    estimated_token_count: Mapped[int] = mapped_column(Integer)
    token_estimator_version: Mapped[str] = mapped_column(String(64))
    unique_search_term_count: Mapped[int] = mapped_column(Integer)
    created_by_type: Mapped[MemoryCreatedByType] = mapped_column(
        Enum(MemoryCreatedByType, native_enum=False, length=32)
    )
    created_by_attempt_id: Mapped[str | None] = mapped_column(
        ForeignKey("llm_attempts.attempt_id", ondelete="SET NULL"),
        index=True,
    )
    content_erased_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        server_default=func.now(),
    )


class LlmMemoryMutation(Base):
    """Record idempotency, outcome, and audited actor evidence for Memory mutations."""

    __tablename__ = "llm_memory_mutations"
    __table_args__ = (
        UniqueConstraint(
            "memory_id",
            "idempotency_key",
            name="uq_memory_mutation_key",
        ),
        Index("ix_memory_mutation_memory_created", "memory_id", "created_at"),
        Index("ix_memory_mutation_operation_created", "operation", "created_at"),
    )

    memory_mutation_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    memory_id: Mapped[str] = mapped_column(
        ForeignKey("llm_memories.memory_id", ondelete="CASCADE"),
        index=True,
    )
    idempotency_key: Mapped[str] = mapped_column(String(128))
    request_hash: Mapped[str] = mapped_column(String(64))
    operation: Mapped[MemoryMutationOperation] = mapped_column(
        Enum(MemoryMutationOperation, native_enum=False, length=32),
        default=MemoryMutationOperation.UPDATE_METADATA,
        index=True,
    )
    actor_type: Mapped[MemoryMutationActorType] = mapped_column(
        Enum(MemoryMutationActorType, native_enum=False, length=32),
        default=MemoryMutationActorType.TRUSTED_CALLER,
    )
    actor_id: Mapped[str | None] = mapped_column(String(128))
    before_version_number: Mapped[int | None] = mapped_column(Integer)
    after_version_number: Mapped[int] = mapped_column(Integer)
    before_status: Mapped[MemoryStatus | None] = mapped_column(
        Enum(MemoryStatus, native_enum=False, length=32)
    )
    after_status: Mapped[MemoryStatus] = mapped_column(
        Enum(MemoryStatus, native_enum=False, length=32)
    )
    change_json: Mapped[dict | None] = mapped_column(JSON)
    result_version_number: Mapped[int] = mapped_column(Integer)
    result_status: Mapped[MemoryStatus] = mapped_column(
        Enum(MemoryStatus, native_enum=False, length=32)
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        server_default=func.now(),
    )


class LlmMemorySource(Base):
    """Link one Memory version to a validated source resource and evidence hash."""

    __tablename__ = "llm_memory_sources"
    __table_args__ = (
        UniqueConstraint(
            "memory_version_id",
            "source_type",
            "source_resource_id",
            name="uq_memory_version_source",
        ),
    )

    memory_source_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    memory_version_id: Mapped[str] = mapped_column(
        ForeignKey("llm_memory_versions.memory_version_id", ondelete="CASCADE"),
        index=True,
    )
    source_type: Mapped[MemorySourceType] = mapped_column(
        Enum(MemorySourceType, native_enum=False, length=32)
    )
    source_resource_id: Mapped[str] = mapped_column(String(128), index=True)
    source_content_hash: Mapped[str] = mapped_column(String(64))
    trust_level: Mapped[MemoryTrustLevel] = mapped_column(
        Enum(MemoryTrustLevel, native_enum=False, length=32),
        default=MemoryTrustLevel.INTERNAL_SYSTEM_RESULT,
    )
    source_order: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        server_default=func.now(),
    )


class LlmMemorySearchTerm(Base):
    """Store one hashed multilingual lexical term for a Memory version."""

    __tablename__ = "llm_memory_search_terms"
    __table_args__ = (
        Index("ix_memory_search_term_hash_version", "term_hash", "memory_version_id"),
    )

    memory_version_id: Mapped[str] = mapped_column(
        ForeignKey("llm_memory_versions.memory_version_id", ondelete="CASCADE"),
        primary_key=True,
    )
    term_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    term_frequency: Mapped[int] = mapped_column(Integer)


class LlmMemoryRetrieval(Base):
    """Record one successful bounded Memory retrieval and its ranking configuration."""

    __tablename__ = "llm_memory_retrievals"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "idempotency_key",
            name="uq_memory_retrieval_user_key",
        ),
        Index(
            "ix_memory_retrieval_user_created_id",
            "user_id",
            "created_at",
            "memory_retrieval_id",
        ),
    )

    memory_retrieval_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.user_id", ondelete="CASCADE"),
        index=True,
    )
    session_id: Mapped[str | None] = mapped_column(
        ForeignKey("llm_sessions.session_id", ondelete="RESTRICT"),
        index=True,
    )
    run_id: Mapped[str | None] = mapped_column(
        ForeignKey("llm_runs.run_id", ondelete="RESTRICT"),
        index=True,
    )
    task_id: Mapped[str | None] = mapped_column(
        ForeignKey("llm_tasks.task_id", ondelete="RESTRICT"),
        index=True,
    )
    query_text: Mapped[str] = mapped_column(Text)
    query_hash: Mapped[str] = mapped_column(String(64))
    memory_types_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    result_limit: Mapped[int] = mapped_column(Integer)
    token_budget: Mapped[int] = mapped_column(Integer)
    candidate_method: Mapped[str] = mapped_column(String(64))
    ranker_version: Mapped[str] = mapped_column(String(64))
    status: Mapped[MemoryRetrievalStatus] = mapped_column(
        Enum(MemoryRetrievalStatus, native_enum=False, length=32)
    )
    idempotency_key: Mapped[str] = mapped_column(String(128))
    request_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        server_default=func.now(),
    )
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class LlmMemoryRetrievalResult(Base):
    """Persist one ranked Memory candidate and its selection evidence."""

    __tablename__ = "llm_memory_retrieval_results"
    __table_args__ = (
        UniqueConstraint(
            "memory_retrieval_id",
            "rank",
            name="uq_memory_retrieval_result_rank",
        ),
        UniqueConstraint(
            "memory_retrieval_id",
            "memory_id",
            name="uq_memory_retrieval_result_memory",
        ),
    )

    memory_retrieval_result_id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=new_id
    )
    memory_retrieval_id: Mapped[str] = mapped_column(
        ForeignKey("llm_memory_retrievals.memory_retrieval_id", ondelete="CASCADE"),
        index=True,
    )
    memory_id: Mapped[str] = mapped_column(
        ForeignKey("llm_memories.memory_id", ondelete="CASCADE"),
        index=True,
    )
    memory_version_id: Mapped[str] = mapped_column(
        ForeignKey("llm_memory_versions.memory_version_id", ondelete="CASCADE"),
        index=True,
    )
    rank: Mapped[int] = mapped_column(Integer)
    total_score: Mapped[int] = mapped_column(Integer)
    lexical_score: Mapped[int] = mapped_column(Integer)
    scope_score: Mapped[int] = mapped_column(Integer)
    importance_score: Mapped[int] = mapped_column(Integer)
    confidence_score: Mapped[int] = mapped_column(Integer)
    recency_score: Mapped[int] = mapped_column(Integer)
    estimated_token_count: Mapped[int] = mapped_column(Integer)
    selected: Mapped[bool] = mapped_column(Boolean)
    exclusion_reason: Mapped[str | None] = mapped_column(String(64))
