"""Knowledge document, version, and retrieval HTTP schemas."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from nexuspilot_api.models import KnowledgeDocumentStatus, KnowledgeVersionStatus
from nexuspilot_api.schemas.base import ApiModel


class KnowledgeDocumentCreate(BaseModel):
    """Validate creation of one stable Knowledge document identity."""

    model_config = ConfigDict(extra="forbid")

    user_id: str = Field(min_length=1, max_length=128)
    session_id: str | None = Field(default=None, min_length=1, max_length=36)
    run_id: str | None = Field(default=None, min_length=1, max_length=36)
    task_id: str | None = Field(default=None, min_length=1, max_length=36)
    document_key: str = Field(min_length=1, max_length=255)
    title: str = Field(min_length=1, max_length=255)


class KnowledgeDocumentUpdate(BaseModel):
    """Validate the only supported document-level transition: deactivation."""

    model_config = ConfigDict(extra="forbid")

    deactivate: bool = True


class KnowledgeVersionCreate(BaseModel):
    """Validate one Knowledge version submission backed by an existing Artifact."""

    model_config = ConfigDict(extra="forbid")

    artifact_id: str = Field(min_length=1, max_length=36)
    parser_version: str = Field(min_length=1, max_length=64)


class KnowledgeDocumentRead(ApiModel):
    """Expose one Knowledge document with its current version pointer."""

    knowledge_document_id: str
    user_id: str
    session_id: str | None
    run_id: str | None
    task_id: str | None
    document_key: str
    title: str
    status: KnowledgeDocumentStatus
    current_version_number: int
    created_at: datetime
    updated_at: datetime


class KnowledgeVersionRead(ApiModel):
    """Expose one immutable Knowledge version processing state."""

    knowledge_document_version_id: str
    knowledge_document_id: str
    version_number: int
    artifact_id: str | None
    artifact_uri: str
    content_hash: str
    parser_version: str
    status: KnowledgeVersionStatus
    chunk_count: int
    created_at: datetime
    activated_at: datetime | None
    failed_at: datetime | None
    error_code: str | None


class KnowledgeRetrievalCreate(BaseModel):
    """Validate one bounded, owner-scoped Knowledge retrieval."""

    model_config = ConfigDict(extra="forbid")

    user_id: str = Field(min_length=1, max_length=128)
    session_id: str | None = Field(default=None, min_length=1, max_length=36)
    run_id: str | None = Field(default=None, min_length=1, max_length=36)
    task_id: str | None = Field(default=None, min_length=1, max_length=36)
    document_id: str | None = Field(default=None, min_length=1, max_length=36)
    query_text: str = Field(min_length=1, max_length=2_000)
    limit: int = Field(default=10, ge=1, le=50)


class KnowledgeRetrievalResultRead(ApiModel):
    """Expose one ranked chunk with stable document and locator evidence."""

    rank: int
    knowledge_document_id: str
    document_key: str
    version_number: int
    knowledge_chunk_id: str
    chunk_order: int
    stable_locator: str
    content_preview: str
    content_hash: str
    lexical_score: int


class KnowledgeRetrievalRead(ApiModel):
    """Expose one completed bounded Knowledge retrieval."""

    user_id: str
    query_text: str
    document_id: str | None
    limit: int
    results: list[KnowledgeRetrievalResultRead]
