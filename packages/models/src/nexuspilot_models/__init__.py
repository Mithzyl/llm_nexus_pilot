"""Provider-neutral model contracts and adapters used by NexusPilot services."""

from nexuspilot_models.contracts import (
    Message,
    MessageRole,
    ModelRequest,
    ModelResponse,
    ProviderName,
    StreamEvent,
    StreamEventType,
    ToolCall,
    ToolDefinition,
)
from nexuspilot_models.errors import ModelProviderError
from nexuspilot_models.provider import ModelProvider
from nexuspilot_models.registry import ProviderRegistry

__all__ = [
    "Message",
    "MessageRole",
    "ModelProvider",
    "ModelProviderError",
    "ModelRequest",
    "ModelResponse",
    "ProviderName",
    "ProviderRegistry",
    "StreamEvent",
    "StreamEventType",
    "ToolCall",
    "ToolDefinition",
]
