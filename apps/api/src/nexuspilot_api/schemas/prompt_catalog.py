"""Prompt template and Model Catalog HTTP schemas."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from nexuspilot_api.models import ModelCatalogStatus, PromptTemplateStatus
from nexuspilot_api.schemas.base import ApiModel


class PromptTemplateCreate(BaseModel):
    """Validate creation of one Prompt template with its first immutable version."""

    model_config = ConfigDict(extra="forbid")

    template_name: str = Field(min_length=1, max_length=128)
    purpose: str | None = Field(default=None, max_length=4_000)
    content_text: str = Field(min_length=1, max_length=40_000)
    variable_schema: dict[str, str] = Field(default_factory=dict)
    created_by_actor_id: str = Field(min_length=1, max_length=128)


class PromptTemplateVersionCreate(BaseModel):
    """Validate one new immutable Prompt template version."""

    model_config = ConfigDict(extra="forbid")

    content_text: str = Field(min_length=1, max_length=40_000)
    variable_schema: dict[str, str] = Field(default_factory=dict)
    created_by_actor_id: str = Field(min_length=1, max_length=128)


class PromptActiveVersionUpdate(BaseModel):
    """Validate one explicit active-version switch for a Prompt template."""

    model_config = ConfigDict(extra="forbid")

    version_number: int = Field(ge=1)
    actor_id: str = Field(min_length=1, max_length=128)


class PromptTemplateRead(ApiModel):
    """Expose one Prompt template with its active immutable version."""

    template_name: str
    purpose: str | None
    status: PromptTemplateStatus
    current_version_number: int
    content_text: str
    variable_schema: dict[str, str]
    created_at: datetime
    updated_at: datetime


class PromptRenderCreate(BaseModel):
    """Validate one render request that never triggers a model call."""

    model_config = ConfigDict(extra="forbid")

    template_name: str = Field(min_length=1, max_length=128)
    version_number: int | None = Field(default=None, ge=1)
    variables: dict[str, str] = Field(default_factory=dict)


class PromptRenderRead(ApiModel):
    """Expose one rendered Prompt with exact version evidence."""

    template_name: str
    version_number: int
    rendered_text: str
    rendered_token_estimate: int
    variable_names: list[str]


class ModelCatalogVersionCreate(BaseModel):
    """Validate one immutable provider/model capability snapshot."""

    model_config = ConfigDict(extra="forbid")

    provider: str = Field(min_length=1, max_length=64)
    model: str = Field(min_length=1, max_length=128)
    catalog_version: str = Field(min_length=1, max_length=64)
    source: str = Field(min_length=1, max_length=128)
    context_window: int = Field(ge=1_024)
    supports_tools: bool = False
    supports_structured_output: bool = False
    supports_vision: bool = False
    supports_caching: bool = False
    supports_streaming: bool = False
    tokenizer_name: str = Field(min_length=1, max_length=64)
    tokenizer_version: str = Field(min_length=1, max_length=64)
    capabilities_json: dict = Field(default_factory=dict)


class ModelCatalogStatusUpdate(BaseModel):
    """Validate one explicit catalog snapshot enablement transition."""

    model_config = ConfigDict(extra="forbid")

    status: ModelCatalogStatus


class ModelCapabilityRead(ApiModel):
    """Expose one enabled model capability or an explicit unknown answer."""

    provider: str
    model: str
    catalog_version: str | None
    known: bool
    context_window: int | None
    supports_tools: bool | None
    supports_structured_output: bool | None
    supports_vision: bool | None
    supports_caching: bool | None
    supports_streaming: bool | None
    tokenizer_name: str | None
    tokenizer_version: str | None
