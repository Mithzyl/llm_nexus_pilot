"""Stable provider-neutral exception taxonomy and transport failure details."""

from typing import Any

from nexuspilot_models.contracts import TransportAttempt


class ModelProviderError(Exception):
    """Describe a safe model failure with retry and persistence metadata."""

    def __init__(
        self,
        error_type: str,
        message: str,
        *,
        retryable: bool = False,
        status_code: int | None = None,
        provider_request_id: str | None = None,
        raw_error: dict[str, Any] | None = None,
        transport_attempts: list[TransportAttempt] | None = None,
    ) -> None:
        """Create a provider error without embedding authentication headers or credentials."""

        super().__init__(message)
        self.error_type = error_type
        self.message = message
        self.retryable = retryable
        self.status_code = status_code
        self.provider_request_id = provider_request_id
        self.raw_error = raw_error or {}
        self.transport_attempts = transport_attempts or []


class ProviderNotRegisteredError(ModelProviderError):
    """Report a configured provider name that has no active adapter."""

    def __init__(self, provider: str) -> None:
        """Create a non-retryable missing-provider error."""

        super().__init__(
            "provider_not_configured",
            f"Provider '{provider}' is not configured.",
        )


class ModelNotAllowedError(ModelProviderError):
    """Report a model that is outside the selected provider's optional allowlist."""

    def __init__(self, provider: str, model: str) -> None:
        """Create a non-retryable provider/model combination error."""

        super().__init__(
            "model_not_allowed",
            f"Model '{model}' is not allowed for provider '{provider}'.",
        )
