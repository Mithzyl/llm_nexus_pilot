"""Explicit model-provider registration and provider/model combination validation."""

from nexuspilot_models.contracts import ProviderName
from nexuspilot_models.errors import ModelNotAllowedError, ProviderNotRegisteredError
from nexuspilot_models.provider import ModelProvider


class ProviderRegistry:
    """Resolve a stable provider name to one configured adapter instance."""

    def __init__(self) -> None:
        """Create an empty registry with no implicit provider discovery."""

        self._providers: dict[ProviderName, ModelProvider] = {}
        self._allowed_models: dict[ProviderName, frozenset[str]] = {}

    def register(
        self,
        name: ProviderName,
        provider: ModelProvider,
        *,
        allowed_models: frozenset[str] | None = None,
    ) -> None:
        """Register an adapter and an optional exact model allowlist once."""

        if name in self._providers:
            raise ValueError(f"Provider '{name}' is already registered")
        self._providers[name] = provider
        self._allowed_models[name] = allowed_models or frozenset()

    def resolve(self, name: ProviderName, model: str) -> ModelProvider:
        """Return an adapter after validating the provider/model combination."""

        provider = self._providers.get(name)
        if provider is None:
            raise ProviderNotRegisteredError(name)
        allowed_models = self._allowed_models[name]
        if allowed_models and model not in allowed_models:
            raise ModelNotAllowedError(name, model)
        return provider

    @property
    def registered_names(self) -> tuple[ProviderName, ...]:
        """Return registered provider names for readiness diagnostics."""

        return tuple(self._providers)
