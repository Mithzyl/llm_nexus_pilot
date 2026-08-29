"""DeepSeek Chat Completions adapter with explicit model reasoning semantics."""

from typing import Any

from nexuspilot_models.contracts import ModelRequest, ProviderName
from nexuspilot_models.errors import ModelProviderError
from nexuspilot_models.providers.openai_compatible import OpenAICompatibleChatProvider
from nexuspilot_models.transport import HttpTransport

_NATIVE_REASONING_EFFORTS = frozenset({"low", "high", "max"})


class DeepSeekChatProvider(OpenAICompatibleChatProvider):
    """Apply DeepSeek-only validation and payload fields over the shared chat codec."""

    def __init__(
        self,
        transport: HttpTransport,
        *,
        base_url: str,
        api_key: str,
    ) -> None:
        """Configure DeepSeek Chat Completions without enabling its Responses endpoint."""

        super().__init__(
            name=ProviderName.DEEPSEEK,
            transport=transport,
            base_url=base_url,
            api_key=api_key,
            supports_json_schema=False,
            supports_json_object_output=True,
            supports_reasoning_configuration=True,
            requires_done_marker=True,
        )

    def _build_payload(self, request: ModelRequest, *, stream: bool) -> dict[str, Any]:
        """Add DeepSeek thinking controls to the shared Chat Completions payload."""

        payload = super()._build_payload(request, stream=stream)
        if request.reasoning is None:
            return payload
        payload["thinking"] = {"type": "enabled" if request.reasoning.enabled else "disabled"}
        provider_effort = self._provider_reasoning_effort(request)
        if provider_effort is not None:
            payload["reasoning_effort"] = provider_effort
        return payload

    def _validate_request(self, request: ModelRequest) -> None:
        """Reject DeepSeek combinations that would be ignored or silently remapped upstream."""

        super()._validate_request(request)
        if any(tool.strict for tool in request.tools):
            raise ModelProviderError(
                "unsupported_capability",
                "DeepSeek strict tool definitions are not enabled for this endpoint.",
            )
        reasoning_is_enabled = request.reasoning is None or request.reasoning.enabled
        if reasoning_is_enabled and request.temperature is not None:
            raise ModelProviderError(
                "invalid_request",
                "DeepSeek temperature is not supported while reasoning is enabled.",
            )
        provider_effort = self._provider_reasoning_effort(request)
        if provider_effort is not None and provider_effort not in _NATIVE_REASONING_EFFORTS:
            raise ModelProviderError(
                "unsupported_capability",
                f"DeepSeek does not support native reasoning effort '{provider_effort}'.",
            )

    @staticmethod
    def _provider_reasoning_effort(request: ModelRequest) -> str | None:
        """Return the resolved DeepSeek effort or a direct low-level contract value."""

        if request.resolved_reasoning is not None:
            provider_value = request.resolved_reasoning.provider_value
            if provider_value is None:
                return None
            if not isinstance(provider_value, str):
                raise ModelProviderError(
                    "unsupported_capability",
                    "DeepSeek reasoning effort mapping must resolve to a string.",
                )
            return provider_value
        if request.reasoning is None or request.reasoning.effort is None:
            return None
        return request.reasoning.effort.value
