"""Stable provider-neutral request, response, and streaming contracts."""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ContractModel(BaseModel):
    """Reject unknown fields so provider-specific parameters cannot leak into public contracts."""

    model_config = ConfigDict(extra="forbid")


class ProviderName(StrEnum):
    """Identify the explicitly supported model provider adapters."""

    OPENAI = "openai"
    DEEPSEEK = "deepseek"
    ANTHROPIC = "anthropic"
    GEMINI = "gemini"
    OPENAI_COMPATIBLE = "openai_compatible"


class MessageRole(StrEnum):
    """Represent conversation roles supported by the current text and tool contract."""

    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class Message(ContractModel):
    """Represent one portable conversation turn without provider-native content blocks."""

    role: MessageRole
    content: str = Field(min_length=1)
    tool_call_id: str | None = Field(default=None, max_length=256)
    tool_name: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def validate_tool_message(self) -> "Message":
        """Require tool messages to identify the call and forbid the field on other roles."""

        if self.role is MessageRole.TOOL and not self.tool_call_id:
            raise ValueError("tool_call_id is required for tool messages")
        if self.role is not MessageRole.TOOL and self.tool_call_id is not None:
            raise ValueError("tool_call_id is only valid for tool messages")
        if self.role is not MessageRole.TOOL and self.tool_name is not None:
            raise ValueError("tool_name is only valid for tool messages")
        return self


class ToolDefinition(ContractModel):
    """Describe a client-executed function using a JSON Schema input contract."""

    name: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    description: str = Field(min_length=1, max_length=4096)
    input_schema: dict[str, Any]
    strict: bool = False


class ToolCall(ContractModel):
    """Represent one normalized function request emitted by a model."""

    id: str
    name: str
    arguments: dict[str, Any]


class TransportAttempt(ContractModel):
    """Capture one physical HTTP attempt for persistence by the calling application."""

    attempt_index: int = Field(ge=1)
    status_code: int | None = None
    latency_ms: int = Field(ge=0)
    error_type: str | None = None
    error_message: str | None = None


class ModelRequest(ContractModel):
    """Define one provider-neutral model generation request."""

    provider: ProviderName
    model: str = Field(min_length=1, max_length=256)
    messages: list[Message] = Field(min_length=1, max_length=1000)
    system_instruction: str | None = Field(default=None, max_length=100_000)
    tools: list[ToolDefinition] = Field(default_factory=list, max_length=128)
    output_schema: dict[str, Any] | None = None
    temperature: float | None = Field(default=None, ge=0, le=2)
    max_output_tokens: int | None = Field(default=None, ge=1, le=1_000_000)
    timeout_seconds: float = Field(default=60, ge=1, le=600)
    metadata: dict[str, str] = Field(default_factory=dict)


class ModelResponse(ContractModel):
    """Return normalized content, accounting, diagnostics, and raw provider evidence."""

    text: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    structured_output: dict[str, Any] | list[Any] | None = None
    finish_reason: str
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    cached_tokens: int | None = Field(default=None, ge=0)
    estimated_cost: str | None = None
    latency_ms: int = Field(ge=0)
    provider_request_id: str | None = None
    raw_response: dict[str, Any]
    transport_attempts: list[TransportAttempt] = Field(default_factory=list)


class StreamEventType(StrEnum):
    """Define the only event names exposed by the NexusPilot SSE interface."""

    STARTED = "response.started"
    TEXT_DELTA = "response.text.delta"
    TOOL_CALL_DELTA = "response.tool_call.delta"
    USAGE = "response.usage"
    COMPLETED = "response.completed"
    FAILED = "response.failed"


class StreamEvent(ContractModel):
    """Represent one ordered normalized stream event."""

    type: StreamEventType
    sequence: int = Field(ge=1)
    data: dict[str, Any] = Field(default_factory=dict)
