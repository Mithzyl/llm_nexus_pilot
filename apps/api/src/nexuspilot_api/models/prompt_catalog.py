"""Prompt template and Model Catalog immutable version models."""

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
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
from nexuspilot_api.models.enums import ModelCatalogStatus, PromptTemplateStatus


class LlmPromptTemplate(TimestampMixin, Base):
    """Represent one stable Prompt template identity with one active version."""

    __tablename__ = "llm_prompt_templates"
    __table_args__ = (
        Index(
            "ix_prompt_template_status_updated",
            "status",
            "updated_at",
            "template_name",
        ),
    )

    template_name: Mapped[str] = mapped_column(String(128), primary_key=True)
    purpose: Mapped[str | None] = mapped_column(Text)
    status: Mapped[PromptTemplateStatus] = mapped_column(
        Enum(PromptTemplateStatus, native_enum=False, length=32),
        default=PromptTemplateStatus.ENABLED,
        index=True,
    )
    current_version_number: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        server_default=func.now(),
        onupdate=func.now(),
    )


class LlmPromptTemplateVersion(Base):
    """Represent one immutable, never-modified Prompt template version."""

    __tablename__ = "llm_prompt_template_versions"
    __table_args__ = (
        UniqueConstraint(
            "template_name",
            "version_number",
            name="uq_prompt_template_version_number",
        ),
        Index(
            "ix_prompt_template_version_name_status",
            "template_name",
            "version_number",
        ),
    )

    template_version_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    template_name: Mapped[str] = mapped_column(
        ForeignKey("llm_prompt_templates.template_name", ondelete="CASCADE"),
        index=True,
    )
    version_number: Mapped[int] = mapped_column(Integer)
    content_text: Mapped[str] = mapped_column(Text)
    variable_schema_json: Mapped[dict] = mapped_column(JSON)
    created_by_actor_id: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        server_default=func.now(),
    )


class LlmPromptTemplateChange(Base):
    """Record the auditable activation history of Prompt template versions."""

    __tablename__ = "llm_prompt_template_changes"
    __table_args__ = (
        Index(
            "ix_prompt_template_change_name_created",
            "template_name",
            "created_at",
            "change_id",
        ),
    )

    change_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    template_name: Mapped[str] = mapped_column(
        ForeignKey("llm_prompt_templates.template_name", ondelete="CASCADE"),
        index=True,
    )
    action: Mapped[str] = mapped_column(String(32))
    version_number: Mapped[int | None] = mapped_column(Integer)
    actor_id: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        server_default=func.now(),
    )


class LlmModelCatalogVersion(Base):
    """Represent one immutable provider/model capability snapshot."""

    __tablename__ = "llm_model_catalog_versions"
    __table_args__ = (
        UniqueConstraint(
            "provider",
            "model",
            "catalog_version",
            name="uq_model_catalog_version",
        ),
        Index(
            "ix_model_catalog_provider_model_status",
            "provider",
            "model",
            "status",
            "catalog_version",
        ),
    )

    catalog_version_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    provider: Mapped[str] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(String(128))
    catalog_version: Mapped[str] = mapped_column(String(64))
    source: Mapped[str] = mapped_column(String(128))
    confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[ModelCatalogStatus] = mapped_column(
        Enum(ModelCatalogStatus, native_enum=False, length=32),
        default=ModelCatalogStatus.ENABLED,
        index=True,
    )
    context_window: Mapped[int] = mapped_column(Integer)
    supports_tools: Mapped[bool] = mapped_column(Boolean, default=False)
    supports_structured_output: Mapped[bool] = mapped_column(Boolean, default=False)
    supports_vision: Mapped[bool] = mapped_column(Boolean, default=False)
    supports_caching: Mapped[bool] = mapped_column(Boolean, default=False)
    supports_streaming: Mapped[bool] = mapped_column(Boolean, default=False)
    tokenizer_name: Mapped[str] = mapped_column(String(64))
    tokenizer_version: Mapped[str] = mapped_column(String(64))
    capabilities_json: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        server_default=func.now(),
    )
