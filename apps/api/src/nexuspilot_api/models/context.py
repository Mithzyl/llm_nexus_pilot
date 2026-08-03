"""Context Build and per-source selection evidence models."""

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

from nexuspilot_api.models.base import Base, new_id, utc_now
from nexuspilot_api.models.enums import (
    ContextBuildStatus,
    ContextSourceSelectionStatus,
)


class LlmContextBuild(Base):
    """Represent one persisted, version-pinned Context Build for a model call."""

    __tablename__ = "llm_context_builds"
    __table_args__ = (
        Index(
            "ix_context_build_user_created",
            "user_id",
            "created_at",
            "context_build_id",
        ),
        Index(
            "ix_context_build_session_created",
            "session_id",
            "created_at",
            "context_build_id",
        ),
    )

    context_build_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.user_id", ondelete="CASCADE"),
        index=True,
    )
    session_id: Mapped[str | None] = mapped_column(
        ForeignKey("llm_sessions.session_id", ondelete="RESTRICT"),
        index=True,
    )
    project_id: Mapped[str | None] = mapped_column(
        ForeignKey("llm_projects.project_id", ondelete="RESTRICT"),
        index=True,
    )
    catalog_version_id: Mapped[str | None] = mapped_column(
        ForeignKey(
            "llm_model_catalog_versions.catalog_version_id", ondelete="RESTRICT"
        ),
        index=True,
    )
    provider: Mapped[str] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(String(128))
    token_budget: Mapped[int] = mapped_column(Integer)
    reserved_output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    recent_message_count: Mapped[int] = mapped_column(Integer, default=12)
    tokenizer_name: Mapped[str] = mapped_column(String(64))
    tokenizer_version: Mapped[str] = mapped_column(String(64))
    input_token_estimate: Mapped[int] = mapped_column(Integer)
    status: Mapped[ContextBuildStatus] = mapped_column(
        Enum(ContextBuildStatus, native_enum=False, length=32),
        default=ContextBuildStatus.COMPLETED,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        server_default=func.now(),
    )


class LlmContextSource(Base):
    """Record one selected or excluded source with its version and token evidence."""

    __tablename__ = "llm_context_sources"
    __table_args__ = (
        UniqueConstraint(
            "context_build_id",
            "source_type",
            "source_id",
            name="uq_context_source_identity",
        ),
        Index("ix_context_source_build_order", "context_build_id", "source_order"),
    )

    context_source_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    context_build_id: Mapped[str] = mapped_column(
        ForeignKey("llm_context_builds.context_build_id", ondelete="CASCADE"),
        index=True,
    )
    source_type: Mapped[str] = mapped_column(String(64))
    source_id: Mapped[str] = mapped_column(String(36))
    source_version: Mapped[str | None] = mapped_column(String(64))
    message_role: Mapped[str | None] = mapped_column(String(32))
    source_order: Mapped[int] = mapped_column(Integer)
    token_estimate: Mapped[int] = mapped_column(Integer)
    selection_status: Mapped[ContextSourceSelectionStatus] = mapped_column(
        Enum(ContextSourceSelectionStatus, native_enum=False, length=32),
        default=ContextSourceSelectionStatus.SELECTED,
    )
    exclusion_reason: Mapped[str | None] = mapped_column(String(128))
    content_hash: Mapped[str | None] = mapped_column(String(64))
    content_text: Mapped[str | None] = mapped_column(Text)
