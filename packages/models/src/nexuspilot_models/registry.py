"""Explicit model-provider registration and provider/model combination validation."""

from nexuspilot_models.contracts import ModelReasoningCapabilities, ProviderName
from nexuspilot_models.errors import ModelNotAllowedError, ProviderNotRegisteredError
from nexuspilot_models.provider import ModelProvider


class ProviderRegistry:
    """Resolve a stable provider name to one configured adapter instance."""

    def __init__(self) -> None:
        """Create an empty registry with no implicit provider discovery."""

        self._providers: dict[ProviderName, ModelProvider] = {}
        self._allowed_models: dict[ProviderName, frozenset[str]] = {}
        self._reasoning_capabilities: dict[
            tuple[ProviderName, str], ModelReasoningCapabilities
        ] = {}

    def register(
        self,
        name: ProviderName,
        provider: ModelProvider,
        *,
        allowed_models: frozenset[str] | None = None,
        reasoning_capabilities_by_model: dict[
            str, ModelReasoningCapabilities
        ] | None = None,
    ) -> None:
        """Register an adapter, exact model allowlist, and truthful reasoning profiles once."""

        if name in self._providers:
            raise ValueError(f"Provider '{name}' is already registered")
        self._providers[name] = provider
        self._allowed_models[name] = allowed_models or frozenset()
        for model, capabilities in (reasoning_capabilities_by_model or {}).items():
            if allowed_models and model not in allowed_models:
                raise ValueError(
                    f"Reasoning capabilities reference non-allowed model '{model}'"
                )
            self._reasoning_capabilities[(name, model)] = capabilities

    def resolve(self, name: ProviderName, model: str) -> ModelProvider:
        """Return an adapter after validating the provider/model combination."""

        provider = self._providers.get(name)
        if provider is None:
            raise ProviderNotRegisteredError(name)
        allowed_models = self._allowed_models[name]
        if allowed_models and model not in allowed_models:
            raise ModelNotAllowedError(name, model)
        return provider

    def reasoning_capabilities(
        self,
        name: ProviderName,
        model: str,
    ) -> ModelReasoningCapabilities:
        """Return a registered model profile or the safe non-reasoning default."""

        self.resolve(name, model)
        return self._reasoning_capabilities.get(
            (name, model),
            ModelReasoningCapabilities(),
        )

    @property
    def registered_names(self) -> tuple[ProviderName, ...]:
        """Return registered provider names for readiness diagnostics."""

        return tuple(self._providers)

    @property
    def configured_models_by_provider(self) -> dict[ProviderName, tuple[str, ...]]:
        """Return deterministic configured model identifiers without exposing adapters."""

        return {
            provider_name: tuple(sorted(self._allowed_models[provider_name]))
            for provider_name in self._providers
        }

    @property
    def reasoning_capabilities_by_provider_model(
        self,
    ) -> dict[ProviderName, dict[str, ModelReasoningCapabilities]]:
        """Return deterministic public capability metadata without exposing adapters."""

        return {
            provider_name: {
                model: self._reasoning_capabilities.get(
                    (provider_name, model),
                    ModelReasoningCapabilities(),
                )
                for model in sorted(self._allowed_models[provider_name])
            }
            for provider_name in self._providers
        }
