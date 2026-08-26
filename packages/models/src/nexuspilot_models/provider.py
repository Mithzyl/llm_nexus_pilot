"""Provider protocol implemented by all model adapters."""

from collections.abc import AsyncIterator
from typing import Protocol

from nexuspilot_models.contracts import (
    ModelRequest,
    ModelResponse,
    ProviderName,
    StreamEvent,
)


class ModelProvider(Protocol):
    """Define the provider capabilities consumed by application services."""

    name: ProviderName

    async def generate(self, request: ModelRequest) -> ModelResponse:
        """Execute one non-streaming request and return a normalized response."""

        ...

    def stream(self, request: ModelRequest) -> AsyncIterator[StreamEvent]:
        """Execute one streaming request and yield normalized ordered events."""

        ...
