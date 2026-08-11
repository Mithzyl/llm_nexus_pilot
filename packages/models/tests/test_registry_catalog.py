"""Model registry discovery tests independent of any provider transport."""

from typing import cast

from nexuspilot_models.contracts import ProviderName
from nexuspilot_models.provider import ModelProvider
from nexuspilot_models.registry import ProviderRegistry


def test_configured_models_are_sorted_and_include_unrestricted_providers() -> None:
    """Return stable model options and preserve an empty unrestricted allowlist."""

    registry = ProviderRegistry()
    registry.register(
        ProviderName.DEEPSEEK,
        cast(ModelProvider, object()),
        allowed_models=frozenset({"deepseek-v4-pro", "deepseek-v4-flash"}),
    )
    registry.register(
        ProviderName.OPENAI_COMPATIBLE,
        cast(ModelProvider, object()),
    )

    assert registry.configured_models_by_provider == {
        ProviderName.DEEPSEEK: ("deepseek-v4-flash", "deepseek-v4-pro"),
        ProviderName.OPENAI_COMPATIBLE: (),
    }
