"""Knowledge document, version, and chunk persistence models."""

from datetime import datetime

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from nexuspilot_api.models.base import Base, TimestampMixin, new_id, utc_now
from nexuspilot_api.models.enums import KnowledgeDocumentStatus, KnowledgeVersionStatus


class LlmKnowledgeDocument(TimestampMixin, Base):
    """Represent one stable Knowledge document identity with a current version pointer."""

    __tablename__ = "llm_knowledge_documents"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "document_key",
            name="uq_knowledge_document_user_key",
        ),
        Index(
            "ix_knowledge_document_user_status_created",
            "user_id",
            "status",
            "created_at",
            "knowledge_document_id",
        ),
    )

    knowledge_document_id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=new_id
    )
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
    document_key: Mapped[str] = mapped_column(String(255))
    title: Mapped[str] = mapped_column(String(255))
    status: Mapped[KnowledgeDocumentStatus] = mapped_column(
        Enum(KnowledgeDocumentStatus, native_enum=False, length=32),
        default=KnowledgeDocumentStatus.PENDING,
        index=True,
    )
    current_version_number: Mapped[int] = mapped_column(Integer, default=0)


class LlmKnowledgeDocumentVersion(Base):
    """Represent one immutable processing state for a Knowledge document version."""

    __tablename__ = "llm_knowledge_document_versions"
    __table_args__ = (
        UniqueConstraint(
            "knowledge_document_id",
            "version_number",
            name="uq_knowledge_document_version_number",
        ),
        Index(
            "ix_knowledge_version_document_status",
            "knowledge_document_id",
            "status",
            "version_number",
        ),
        Index("ix_knowledge_version_artifact", "artifact_id"),
    )

    knowledge_document_version_id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=new_id
    )
    knowledge_document_id: Mapped[str] = mapped_column(
        ForeignKey("llm_knowledge_documents.knowledge_document_id", ondelete="CASCADE"),
        index=True,
    )
    version_number: Mapped[int] = mapped_column(Integer)
    artifact_id: Mapped[str | None] = mapped_column(
        ForeignKey("llm_artifacts.artifact_id", ondelete="RESTRICT"),
        index=True,
    )
    artifact_uri: Mapped[str] = mapped_column(String(1024))
    content_hash: Mapped[str] = mapped_column(String(64))
    parser_version: Mapped[str] = mapped_column(String(64))
    status: Mapped[KnowledgeVersionStatus] = mapped_column(
        Enum(KnowledgeVersionStatus, native_enum=False, length=32),
        default=KnowledgeVersionStatus.PENDING,
    )
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        server_default=func.now(),
    )
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(128))


class LlmKnowledgeChunk(Base):
    """Represent one bounded normalized chunk with a stable locator inside its version."""

    __tablename__ = "llm_knowledge_chunks"
    __table_args__ = (
        UniqueConstraint(
            "knowledge_document_version_id",
            "chunk_order",
            name="uq_knowledge_chunk_order",
        ),
        Index(
            "ix_knowledge_chunk_version_order",
            "knowledge_document_version_id",
            "chunk_order",
        ),
        Index("ix_knowledge_chunk_hash", "content_hash"),
    )

    knowledge_chunk_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    knowledge_document_version_id: Mapped[str] = mapped_column(
        ForeignKey(
            "llm_knowledge_document_versions.knowledge_document_version_id",
            ondelete="CASCADE",
        ),
        index=True,
    )
    chunk_order: Mapped[int] = mapped_column(Integer)
    content_text: Mapped[str] = mapped_column(Text)
    stable_locator: Mapped[str] = mapped_column(String(512))
    artifact_uri: Mapped[str] = mapped_column(String(1024))
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    token_estimate: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        server_default=func.now(),
    )
