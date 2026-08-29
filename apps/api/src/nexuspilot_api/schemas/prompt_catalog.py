"""Prompt template and Model Catalog HTTP schemas."""

import re
from datetime import datetime
from typing import Literal

from nexuspilot_models.contracts import (
    ModelReasoningCapabilities,
    ProviderName,
    ReasoningEffort,
)
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

from nexuspilot_api.models import (
    ModelCatalogStatus,
    ModelRegistrationStatus,
    PromptTemplateStatus,
)
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
        name for name in variable_schema if not PROMPT_VARIABLE_NAME_PATTERN.fullmatch(name)
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


class PromptTemplateOptionRead(ApiModel):
    """Expose one enabled Prompt version for a product selector without its source body."""

    template_name: str
    purpose: str | None
    current_version_number: int
    variable_schema: dict[str, PromptVariableType]


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


class RegisteredModelCreate(BaseModel):
    """Validate explicit registration of one stable provider/model identity."""

    model_config = ConfigDict(extra="forbid")

    provider: ProviderName
    model_identifier: str = Field(min_length=1, max_length=128)
    display_name: str = Field(min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=4_000)
    status: ModelRegistrationStatus = ModelRegistrationStatus.ENABLED
    sort_order: int = Field(default=0, ge=-1_000_000, le=1_000_000)
    metadata_json: dict = Field(default_factory=dict)
    reasoning_supported_efforts: list[ReasoningEffort] | None = None
    reasoning_supports_disable: bool | None = None
    reasoning_default_enabled: bool | None = None
    reasoning_default_effort: ReasoningEffort | None = None
    reasoning_effort_mapping: dict[ReasoningEffort, StrictStr | StrictInt] | None = None
    reasoning_profile_version: str | None = Field(default=None, min_length=1, max_length=64)

    @model_validator(mode="after")
    def validate_reasoning_policy(self) -> "RegisteredModelCreate":
        """Reject partial or internally inconsistent explicit reasoning policies."""

        policy_fields = {
            "reasoning_supported_efforts",
            "reasoning_supports_disable",
            "reasoning_default_enabled",
            "reasoning_default_effort",
            "reasoning_effort_mapping",
            "reasoning_profile_version",
        }
        supplied_fields = policy_fields & self.model_fields_set
        if supplied_fields and supplied_fields != policy_fields:
            raise ValueError("Explicit reasoning policy must provide every policy field")
        if supplied_fields:
            if any(getattr(self, field_name) is None for field_name in policy_fields):
                raise ValueError("Explicit reasoning policy fields cannot be null")
            validate_model_reasoning_policy(
                supported_efforts=self.reasoning_supported_efforts or [],
                default_enabled=bool(self.reasoning_default_enabled),
                default_effort=self.reasoning_default_effort,
                effort_mapping=self.reasoning_effort_mapping or {},
            )
        return self


class RegisteredModelUpdate(BaseModel):
    """Validate mutable display and availability fields for one registered model."""

    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=4_000)
    status: ModelRegistrationStatus | None = None
    sort_order: int | None = Field(default=None, ge=-1_000_000, le=1_000_000)
    metadata_json: dict | None = None
    reasoning_supported_efforts: list[ReasoningEffort] | None = None
    reasoning_supports_disable: bool | None = None
    reasoning_default_enabled: bool | None = None
    reasoning_default_effort: ReasoningEffort | None = None
    reasoning_effort_mapping: dict[ReasoningEffort, StrictStr | StrictInt] | None = None
    reasoning_profile_version: str | None = Field(default=None, min_length=1, max_length=64)

    @model_validator(mode="after")
    def require_one_update(self) -> "RegisteredModelUpdate":
        """Reject empty PATCH bodies that cannot change the registered model."""

        if not self.model_fields_set:
            raise ValueError("At least one registered model field must be supplied")
        non_nullable_fields = {"display_name", "status", "sort_order", "metadata_json"}
        non_nullable_fields.update(
            {
                "reasoning_supported_efforts",
                "reasoning_supports_disable",
                "reasoning_default_enabled",
                "reasoning_default_effort",
                "reasoning_effort_mapping",
                "reasoning_profile_version",
            }
        )
        if any(
            field_name in self.model_fields_set and getattr(self, field_name) is None
            for field_name in non_nullable_fields
        ):
            raise ValueError("Registered model update cannot clear a required field")
        return self


class RegisteredModelRead(ApiModel):
    """Expose stable registered-model fields without provider credentials."""

    model_id: str
    provider: ProviderName
    model_identifier: str
    display_name: str
    description: str | None
    status: ModelRegistrationStatus
    sort_order: int
    metadata_json: dict
    reasoning_supported_efforts: list[ReasoningEffort]
    reasoning_supports_disable: bool
    reasoning_default_enabled: bool
    reasoning_default_effort: ReasoningEffort
    reasoning_effort_mapping: dict[ReasoningEffort, str | int]
    reasoning_profile_version: str
    created_at: datetime
    updated_at: datetime


class RegisteredModelBootstrapRead(BaseModel):
    """Report explicit Registry-to-directory initialization without exposing settings."""

    created_count: int
    existing_count: int


ModelUnavailableReason = Literal[
    "model_disabled",
    "provider_unavailable",
    "model_not_allowed",
    "capability_unknown",
    "streaming_unsupported",
    "structured_output_unsupported",
    "tools_unsupported",
    "vision_unsupported",
    "reasoning_profile_invalid",
]


class ModelDirectoryEntryRead(ApiModel):
    """Expose one stable model with current Registry and capability availability evidence."""

    model_id: str
    provider: ProviderName
    model_identifier: str
    display_name: str
    description: str | None
    status: ModelRegistrationStatus
    sort_order: int
    metadata_json: dict
    registry_available: bool
    selectable: bool
    unavailable_reason: ModelUnavailableReason | None
    catalog_version_id: str | None
    catalog_version: str | None
    context_window: int | None
    supports_tools: bool | None
    supports_structured_output: bool | None
    supports_vision: bool | None
    supports_caching: bool | None
    supports_streaming: bool | None
    tokenizer_name: str | None
    tokenizer_version: str | None
    reasoning_capabilities: ModelReasoningCapabilities
    reasoning_supported_efforts: list[ReasoningEffort]
    reasoning_supports_disable: bool
    reasoning_default_enabled: bool
    reasoning_default_effort: ReasoningEffort
    reasoning_profile_version: str


def validate_model_reasoning_policy(
    *,
    supported_efforts: list[ReasoningEffort],
    default_enabled: bool,
    default_effort: ReasoningEffort | None,
    effort_mapping: dict[ReasoningEffort, str | int],
) -> None:
    """Require an enabled medium default and one explicit mapping per exposed effort."""

    if not default_enabled:
        raise ValueError("Registered models must enable reasoning by default")
    if default_effort is not ReasoningEffort.MEDIUM:
        raise ValueError("Registered models must use medium as the default reasoning effort")
    if len(supported_efforts) != len(set(supported_efforts)):
        raise ValueError("reasoning_supported_efforts cannot contain duplicates")
    if supported_efforts:
        if ReasoningEffort.MEDIUM not in supported_efforts:
            raise ValueError("Enabled reasoning profiles must support medium")
        if set(effort_mapping) != set(supported_efforts):
            raise ValueError("Every supported reasoning effort requires one explicit mapping")
    elif effort_mapping:
        raise ValueError("Reasoning mappings require supported efforts")
