"""DeepSeek Chat Completions adapter with explicit model reasoning semantics."""

from typing import Any

from nexuspilot_models.contracts import ModelRequest, ProviderName, ReasoningEffort
from nexuspilot_models.errors import ModelProviderError
from nexuspilot_models.providers.openai_compatible import OpenAICompatibleChatProvider
from nexuspilot_models.transport import HttpTransport


_REASONING_EFFORTS_BY_MODEL: dict[str, frozenset[ReasoningEffort]] = {
    "deepseek-v4-flash": frozenset(
        {ReasoningEffort.LOW, ReasoningEffort.HIGH, ReasoningEffort.MAX}
    ),
    "deepseek-v4-pro": frozenset({ReasoningEffort.HIGH, ReasoningEffort.MAX}),
}


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
            supports_reasoning_configuration=True,
            requires_done_marker=True,
        )

    def _build_payload(self, request: ModelRequest, *, stream: bool) -> dict[str, Any]:
        """Add DeepSeek thinking controls to the shared Chat Completions payload."""

        payload = super()._build_payload(request, stream=stream)
        if request.reasoning is None:
            return payload
        payload["thinking"] = {
            "type": "enabled" if request.reasoning.enabled else "disabled"
        }
        if request.reasoning.effort is not None:
            payload["reasoning_effort"] = request.reasoning.effort.value
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
        if request.reasoning is None or request.reasoning.effort is None:
            return
        supported_efforts = _REASONING_EFFORTS_BY_MODEL.get(request.model)
        if supported_efforts is None:
            raise ModelProviderError(
                "unsupported_capability",
                f"DeepSeek model '{request.model}' has no declared reasoning effort profile.",
            )
        if request.reasoning.effort not in supported_efforts:
            raise ModelProviderError(
                "unsupported_capability",
                f"DeepSeek model '{request.model}' does not support reasoning effort "
                f"'{request.reasoning.effort.value}'.",
            )
