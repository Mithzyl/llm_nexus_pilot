"""Model-level reasoning policy resolution and audit-contract tests."""

import pytest
from nexuspilot_models.contracts import (
    Message,
    MessageRole,
    ModelRequest,
    ProviderName,
    ReasoningConfiguration,
    ReasoningConfigurationSource,
    ReasoningEffort,
)
from nexuspilot_models.errors import ModelProviderError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from nexuspilot_api.models import LlmRegisteredModel, ModelRegistrationStatus
from nexuspilot_api.services.model_reasoning_policy_service import (
    resolve_registered_model_reasoning,
)


def _request(
    reasoning: ReasoningConfiguration | None = None,
) -> ModelRequest:
    """Build one DeepSeek request whose effective reasoning is resolved from storage."""

    return ModelRequest(
        provider=ProviderName.DEEPSEEK,
        model="deepseek-v4-flash",
        messages=[Message(role=MessageRole.USER, content="Hello")],
        reasoning=reasoning,
    )


async def _create_registered_model(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Persist one complete model reasoning profile for resolver tests."""

    async with session_factory() as db_session:
        db_session.add(
            LlmRegisteredModel(
                model_id="reasoning-model-id",
                provider=ProviderName.DEEPSEEK,
                model_identifier="deepseek-v4-flash",
                display_name="DeepSeek V4 Flash",
                description=None,
                status=ModelRegistrationStatus.ENABLED,
                sort_order=1,
                metadata_json={},
                reasoning_supported_efforts_json=["minimal", "medium", "xhigh"],
                reasoning_supports_disable=True,
                reasoning_default_enabled=True,
                reasoning_default_effort="medium",
                reasoning_effort_mapping_json={
                    "minimal": "low",
                    "medium": "high",
                    "xhigh": "high",
                },
                reasoning_profile_version="deepseek.reasoning.2026-08-28",
            )
        )
        await db_session.commit()


async def test_model_reasoning_policy_defaults_missing_request_to_medium(
    test_database_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Verify omission resolves to the platform medium default and audited native value."""

    await _create_registered_model(test_database_session_factory)

    async with test_database_session_factory() as db_session:
        resolved_request = await resolve_registered_model_reasoning(
            db_session,
            _request(),
        )

    assert resolved_request.reasoning == ReasoningConfiguration(
        enabled=True,
        effort=ReasoningEffort.MEDIUM,
    )
    assert resolved_request.resolved_reasoning is not None
    assert resolved_request.resolved_reasoning.source is ReasoningConfigurationSource.MODEL_DEFAULT
    assert resolved_request.resolved_reasoning.provider_value == "high"
    assert resolved_request.resolved_reasoning.profile_version == "deepseek.reasoning.2026-08-28"


async def test_model_reasoning_policy_maps_explicit_xhigh_without_silent_fallback(
    test_database_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Verify an explicitly supported portable level uses its recorded provider mapping."""

    await _create_registered_model(test_database_session_factory)

    async with test_database_session_factory() as db_session:
        resolved_request = await resolve_registered_model_reasoning(
            db_session,
            _request(ReasoningConfiguration(enabled=True, effort=ReasoningEffort.XHIGH)),
        )

    assert resolved_request.reasoning is not None
    assert resolved_request.reasoning.effort is ReasoningEffort.XHIGH
    assert resolved_request.resolved_reasoning is not None
    assert resolved_request.resolved_reasoning.source is ReasoningConfigurationSource.REQUEST
    assert resolved_request.resolved_reasoning.provider_value == "high"


async def test_model_reasoning_policy_rejects_unlisted_effort_before_provider_dispatch(
    test_database_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Verify unsupported portable levels fail instead of being rounded at call time."""

    await _create_registered_model(test_database_session_factory)

    async with test_database_session_factory() as db_session:
        with pytest.raises(ModelProviderError, match="does not support") as caught:
            await resolve_registered_model_reasoning(
                db_session,
                _request(ReasoningConfiguration(enabled=True, effort=ReasoningEffort.MAX)),
            )

    assert caught.value.error_type == "unsupported_capability"


async def test_model_reasoning_policy_records_explicit_disable(
    test_database_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Verify a model that supports disabling emits a resolved disabled configuration."""

    await _create_registered_model(test_database_session_factory)

    async with test_database_session_factory() as db_session:
        resolved_request = await resolve_registered_model_reasoning(
            db_session,
            _request(ReasoningConfiguration(enabled=False)),
        )

    assert resolved_request.reasoning == ReasoningConfiguration(enabled=False)
    assert resolved_request.resolved_reasoning is not None
    assert resolved_request.resolved_reasoning.source is ReasoningConfigurationSource.REQUEST
    assert resolved_request.resolved_reasoning.provider_value is None
