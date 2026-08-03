"""Prompt template and Model Catalog HTTP schemas."""

import re
from datetime import datetime
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    model_validator,
)

from nexuspilot_api.models import ModelCatalogStatus, PromptTemplateStatus
from nexuspilot_api.schemas.base import ApiModel

PROMPT_VARIABLE_PATTERN = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
PROMPT_VARIABLE_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
PromptVariableType = Literal["string", "integer", "number", "boolean"]
PromptVariableValue = StrictStr | StrictInt | StrictFloat | StrictBool


def validate_prompt_variable_contract(
    content_text: str,
    variable_schema: dict[str, PromptVariableType],
) -> None:
    """Require declared variable names and template placeholders to match exactly."""

    if len(variable_schema) > 64:
        raise ValueError("Prompt templates support at most 64 variables")
    invalid_names = [
        name
        for name in variable_schema
        if not PROMPT_VARIABLE_NAME_PATTERN.fullmatch(name)
    ]
    if invalid_names:
        raise ValueError(f"Invalid prompt variable names: {sorted(invalid_names)}")
    placeholders = set(PROMPT_VARIABLE_PATTERN.findall(content_text))
    declared = set(variable_schema)
    if placeholders != declared:
        raise ValueError("Prompt placeholders must exactly match variable_schema")


class PromptTemplateCreate(BaseModel):
    """Validate creation of one Prompt template with its first immutable version."""

    model_config = ConfigDict(extra="forbid")

    template_name: str = Field(min_length=1, max_length=128)
    purpose: str | None = Field(default=None, max_length=4_000)
    content_text: str = Field(min_length=1, max_length=40_000)
    variable_schema: dict[str, PromptVariableType] = Field(default_factory=dict)
    created_by_actor_id: str = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def validate_variable_contract(self) -> "PromptTemplateCreate":
        """Validate the first immutable version's placeholder contract."""

        validate_prompt_variable_contract(self.content_text, self.variable_schema)
        return self


class PromptTemplateVersionCreate(BaseModel):
    """Validate one new immutable Prompt template version."""

    model_config = ConfigDict(extra="forbid")

    content_text: str = Field(min_length=1, max_length=40_000)
    variable_schema: dict[str, PromptVariableType] = Field(default_factory=dict)
    created_by_actor_id: str = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def validate_variable_contract(self) -> "PromptTemplateVersionCreate":
        """Validate a new immutable version's placeholder contract."""

        validate_prompt_variable_contract(self.content_text, self.variable_schema)
        return self


class PromptActiveVersionUpdate(BaseModel):
    """Validate one explicit active-version switch for a Prompt template."""

    model_config = ConfigDict(extra="forbid")

    version_number: int = Field(ge=1)
    actor_id: str = Field(min_length=1, max_length=128)


class PromptTemplateStatusUpdate(BaseModel):
    """Validate one explicit Prompt template status transition."""

    model_config = ConfigDict(extra="forbid")

    status: PromptTemplateStatus
    actor_id: str = Field(min_length=1, max_length=128)


class PromptTemplateRead(ApiModel):
    """Expose one Prompt template with its active immutable version."""

    template_name: str
    purpose: str | None
    status: PromptTemplateStatus
    current_version_number: int
    content_text: str
    variable_schema: dict[str, PromptVariableType]
    created_at: datetime
    updated_at: datetime


class PromptRenderCreate(BaseModel):
    """Validate one render request that never triggers a model call."""

    model_config = ConfigDict(extra="forbid")

    template_name: str = Field(min_length=1, max_length=128)
    version_number: int | None = Field(default=None, ge=1)
    variables: dict[str, PromptVariableValue] = Field(default_factory=dict)


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
    catalog_version_id: str | None
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
