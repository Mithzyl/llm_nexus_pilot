"""Resolve user-facing reasoning controls through versioned registered-model policy."""

from typing import Any

from nexuspilot_models.contracts import (
    ModelRequest,
    ProviderName,
    ReasoningConfiguration,
    ReasoningConfigurationSource,
    ReasoningEffort,
    ResolvedReasoningConfiguration,
)
from nexuspilot_models.errors import ModelProviderError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nexuspilot_api.models import LlmRegisteredModel, ModelRegistrationStatus


async def resolve_registered_model_reasoning(
    db_session: AsyncSession,
    request: ModelRequest,
) -> ModelRequest:
    """Resolve one request against its model policy before any Provider dispatch.

    Registered models receive an explicit effective configuration unless their
    Strata profile deliberately leaves controls to the provider default. Requests
    for legacy unregistered model identities retain their existing contract so
    internal migrations and low-level adapter tests are not silently reinterpreted.
    Invalid or unsupported registered-model choices fail without calling a Provider.
    """

    reasoning, resolved_reasoning = await resolve_registered_model_reasoning_configuration(
        db_session,
        provider=request.provider,
        model=request.model,
        requested_reasoning=request.reasoning,
    )
    if resolved_reasoning is None:
        return request
    return request.model_copy(
        update={
            "reasoning": reasoning,
            "resolved_reasoning": resolved_reasoning,
        }
    )


async def resolve_registered_model_reasoning_configuration(
    db_session: AsyncSession,
    *,
    provider: ProviderName,
    model: str,
    requested_reasoning: ReasoningConfiguration | None,
    require_registration: bool = False,
) -> tuple[ReasoningConfiguration | None, ResolvedReasoningConfiguration | None]:
    """Resolve one model's effective reasoning configuration without dispatching it.

    Product surfaces can set ``require_registration`` to reject role bindings that
    bypass the stable model directory. Low-level gateway callers retain the legacy
    behavior of passing an unregistered model request through unchanged.
    """

    registered_model = await db_session.scalar(
        select(LlmRegisteredModel).where(
            LlmRegisteredModel.provider == provider,
            LlmRegisteredModel.model_identifier == model,
        )
    )
    if registered_model is None:
        if require_registration:
            raise ModelProviderError(
                "model_not_allowed",
                f"Model '{model}' is not registered in the stable model directory.",
            )
        return requested_reasoning, None
    if registered_model.status is not ModelRegistrationStatus.ENABLED:
        raise ModelProviderError(
            "model_not_allowed",
            f"Model '{model}' is disabled in the registered model directory.",
        )

    # Strata currently has no verified reasoning-control codec. An empty profile
    # means provider defaults, not an invented medium effort or disabled thinking.
    if (
        provider is ProviderName.STRATA
        and registered_model.reasoning_supported_efforts_json == []
        and registered_model.reasoning_effort_mapping_json == {}
        and not registered_model.reasoning_supports_disable
    ):
        if requested_reasoning is not None:
            raise ModelProviderError(
                "unsupported_capability", "Strata reasoning controls are not configured.",
            )
        return None, None
    supported_efforts, effort_mapping, default_effort = _validated_policy(registered_model)
    source = (
        ReasoningConfigurationSource.MODEL_DEFAULT
        if requested_reasoning is None
        else ReasoningConfigurationSource.REQUEST
    )
    reasoning_enabled = (
        registered_model.reasoning_default_enabled
        if requested_reasoning is None
        else requested_reasoning.enabled
    )
    if not reasoning_enabled:
        if not registered_model.reasoning_supports_disable:
            raise ModelProviderError(
                "unsupported_capability",
                f"Model '{model}' does not support disabling reasoning.",
            )
        return (
            ReasoningConfiguration(enabled=False),
            ResolvedReasoningConfiguration(
                source=source,
                profile_version=registered_model.reasoning_profile_version,
                provider_value=None,
            ),
        )

    effective_effort = (
        requested_reasoning.effort
        if requested_reasoning is not None and requested_reasoning.effort is not None
        else default_effort
    )
    if effective_effort not in supported_efforts:
        raise ModelProviderError(
            "unsupported_capability",
            f"Model '{model}' does not support reasoning effort '{effective_effort.value}'.",
        )
    return (
        ReasoningConfiguration(
            enabled=True,
            effort=effective_effort,
        ),
        ResolvedReasoningConfiguration(
            source=source,
            profile_version=registered_model.reasoning_profile_version,
            provider_value=effort_mapping[effective_effort],
        ),
    )


def _validated_policy(
    registered_model: LlmRegisteredModel,
) -> tuple[set[ReasoningEffort], dict[ReasoningEffort, str | int], ReasoningEffort]:
    """Parse one stored profile and reject incomplete or ambiguous provider mappings."""

    try:
        supported_effort_values = registered_model.reasoning_supported_efforts_json
        provider_mapping_values = registered_model.reasoning_effort_mapping_json
        if not isinstance(supported_effort_values, list):
            raise TypeError("supported efforts must be a list")
        if not isinstance(provider_mapping_values, dict):
            raise TypeError("effort mapping must be an object")
        supported_efforts = {ReasoningEffort(effort) for effort in supported_effort_values}
        if len(supported_efforts) != len(supported_effort_values):
            raise ValueError("supported efforts contain duplicates")
        default_effort = ReasoningEffort(registered_model.reasoning_default_effort)
        effort_mapping = {
            ReasoningEffort(effort): _validated_provider_value(provider_value)
            for effort, provider_value in provider_mapping_values.items()
        }
        if not registered_model.reasoning_default_enabled:
            raise ValueError("reasoning default must be enabled")
        if default_effort is not ReasoningEffort.MEDIUM:
            raise ValueError("reasoning default must be medium")
        if default_effort not in supported_efforts:
            raise ValueError("reasoning default is unsupported")
        if set(effort_mapping) != supported_efforts:
            raise ValueError("supported efforts and mappings differ")
        if not registered_model.reasoning_profile_version:
            raise ValueError("reasoning profile version is missing")
    except (KeyError, TypeError, ValueError) as exc:
        raise ModelProviderError(
            "unsupported_capability",
            f"Model '{registered_model.model_identifier}' has an invalid reasoning profile.",
        ) from exc
    try:
        validate_provider_reasoning_mapping(
            registered_model.provider,
            effort_mapping,
        )
    except ValueError as exc:
        raise ModelProviderError(
            "unsupported_capability",
            f"Model '{registered_model.model_identifier}' has an invalid reasoning profile.",
        ) from exc
    return supported_efforts, effort_mapping, default_effort


def _validated_provider_value(provider_value: Any) -> str | int:
    """Accept only non-empty strings or integers as provider-native mapping values."""

    if isinstance(provider_value, bool) or not isinstance(provider_value, str | int):
        raise TypeError("provider reasoning value must be a string or integer")
    if isinstance(provider_value, str) and not provider_value.strip():
        raise ValueError("provider reasoning value cannot be empty")
    return provider_value


def validate_provider_reasoning_mapping(
    provider: ProviderName,
    effort_mapping: dict[ReasoningEffort, str | int],
) -> None:
    """Reject mapping values that the selected official Provider codec cannot encode."""

    allowed_string_values_by_provider = {
        ProviderName.OPENAI: {"minimal", "low", "medium", "high", "xhigh"},
        ProviderName.DEEPSEEK: {"low", "high", "max"},
        ProviderName.ANTHROPIC: {"low", "medium", "high", "xhigh", "max"},
        ProviderName.GEMINI: {"minimal", "low", "medium", "high"},
    }
    if provider in {ProviderName.OPENAI_COMPATIBLE, ProviderName.STRATA}:
        if effort_mapping:
            raise ValueError(
                "OpenAI-compatible models require a dedicated reasoning codec before mapping"
            )
        return
    allowed_string_values = allowed_string_values_by_provider[provider]
    for provider_value in effort_mapping.values():
        if provider is ProviderName.GEMINI and isinstance(provider_value, int):
            if isinstance(provider_value, bool) or provider_value < -1:
                raise ValueError("Gemini thinking budget must be -1 or non-negative")
            continue
        if not isinstance(provider_value, str) or provider_value not in allowed_string_values:
            raise ValueError(
                f"Provider '{provider.value}' does not support reasoning value '{provider_value}'."
            )
