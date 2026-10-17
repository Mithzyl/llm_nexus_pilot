"""Strata Chat Completions using SSE for both incremental and collected responses."""

from collections.abc import AsyncIterator

from nexuspilot_models.contracts import (
    ModelRequest,
    ModelResponse,
    ProviderName,
    StreamEvent,
    StreamEventType,
)
from nexuspilot_models.errors import ModelProviderError
from nexuspilot_models.providers.openai_compatible import OpenAICompatibleChatProvider
from nexuspilot_models.transport import HttpTransport


class StrataChatProvider(OpenAICompatibleChatProvider):
    """Keep Agent calls on the same streaming transport used by quick replies."""

    def __init__(
        self, transport: HttpTransport, *, base_url: str, api_key: str
    ) -> None:
        """Declare JSON-object output only; reasoning controls and native schema stay disabled."""
        super().__init__(
            name=ProviderName.STRATA,
            transport=transport,
            base_url=base_url,
            api_key=api_key,
            supports_json_schema=False,
            supports_json_object_output=True,
            requires_done_marker=True,
        )

    async def generate(self, request: ModelRequest) -> ModelResponse:
        """Collect a complete SSE response without treating a partial stream as a final result."""
        response = None
        async for event in self.stream(request):
            if event.type is StreamEventType.COMPLETED:
                response = ModelResponse.model_validate(event.data["response"])
        if response is None:
            raise ModelProviderError(
                "response_parse_error", "Strata stream has no final response."
            )
        return response

    async def stream(self, request: ModelRequest) -> AsyncIterator[StreamEvent]:
        """Reject empty completed streams for both modes instead of persisting false success."""
        async for event in super().stream(request):
            if event.type is StreamEventType.COMPLETED:
                response = ModelResponse.model_validate(event.data["response"])
                if not response.text and not response.tool_calls:
                    raise ModelProviderError(
                        "response_parse_error",
                        "Strata stream contains no text or tool output.",
                        transport_attempts=response.transport_attempts,
                    )
            yield event
