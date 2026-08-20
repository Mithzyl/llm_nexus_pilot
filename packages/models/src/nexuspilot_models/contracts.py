"""Stable provider-neutral request, response, and streaming contracts."""

from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


MAX_PROVIDER_OUTPUT_TOKENS = 65_536


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


class ReasoningEffort(StrEnum):
    """Identify the portable reasoning-strength levels supported by the public contract."""

    LOW = "low"
    HIGH = "high"
    MAX = "max"


class ReasoningPresentationCapability(StrEnum):
    """Describe the most detailed reasoning presentation a model can provide."""

    RAW = "raw"
    SUMMARY = "summary"
    HIDDEN = "hidden"
    NONE = "none"


class ReasoningContinuationMode(StrEnum):
    """Describe how a provider continues reasoning state across related requests."""

    RAW_REASONING_REPLAY = "raw-reasoning-replay"
    ENCRYPTED_ITEM_REPLAY = "encrypted-item-replay"
    RESPONSE_ID = "response-id"
    NONE = "none"


class ReasoningPresentationKind(StrEnum):
    """Distinguish raw provider reasoning, provider summaries, and status-only evidence."""

    RAW = "reasoning.raw"
    SUMMARY = "reasoning.summary"
    STATUS = "reasoning.status"


class ReasoningBlockStatus(StrEnum):
    """Represent the independent lifecycle of one normalized reasoning block."""

    RUNNING = "running"
    COMPLETED = "completed"
    INTERRUPTED = "interrupted"


class ReasoningDisplayPolicy(StrEnum):
    """Limit the reasoning detail that a caller is allowed to receive."""

    HIDDEN = "hidden"
    SUMMARY_ONLY = "summary-only"
    PROVIDER_VISIBLE = "provider-visible"


class ProviderContinuationKind(StrEnum):
    """Identify private provider state without exposing its payload to public DTOs."""

    DEEPSEEK_RAW_REASONING = "deepseek.raw-reasoning-replay.v1"
    OPENAI_ENCRYPTED_ITEMS = "openai.encrypted-items.v1"
    OPENAI_RESPONSE_ID = "openai.response-id.v1"


class ModelReasoningCapabilities(ContractModel):
    """Declare presentation, streaming, accounting, and continuation support for one model."""

    presentation: ReasoningPresentationCapability = ReasoningPresentationCapability.NONE
    supports_streaming_presentation: bool = False
    continuation: ReasoningContinuationMode = ReasoningContinuationMode.NONE
    supports_reasoning_tokens: bool = False


class ReasoningPresentation(ContractModel):
    """Carry one truthful provider-neutral reasoning block candidate."""

    kind: ReasoningPresentationKind
    block_id: str = Field(min_length=1, max_length=128)
    status: ReasoningBlockStatus
    text: str | None = None
    reasoning_tokens: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_text_matches_kind(self) -> "ReasoningPresentation":
        """Require visible kinds to contain text and forbid fabricated status text."""

        if self.kind is ReasoningPresentationKind.STATUS:
            if self.text is not None:
                raise ValueError("reasoning.status cannot contain text")
            return self
        if not self.text:
            raise ValueError("visible reasoning presentations require non-empty text")
        return self


class ProviderContinuationState(ContractModel):
    """Hold sensitive provider continuation data only while inside trusted backend code."""

    kind: ProviderContinuationKind
    provider_response_id: str | None = Field(default=None, max_length=512)
    raw_reasoning_for_tool_continuation: str | None = None
    encrypted_reasoning_items: list[dict[str, Any]] = Field(default_factory=list)
    tool_call_ids: list[str] = Field(default_factory=list)
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_kind_payload(self) -> "ProviderContinuationState":
        """Reject continuation records whose payload does not match the declared kind."""

        if self.kind is ProviderContinuationKind.DEEPSEEK_RAW_REASONING:
            if not self.raw_reasoning_for_tool_continuation:
                raise ValueError("DeepSeek continuation requires raw reasoning")
        elif self.kind is ProviderContinuationKind.OPENAI_ENCRYPTED_ITEMS:
            if not self.encrypted_reasoning_items:
                raise ValueError("OpenAI encrypted-item continuation requires output items")
        elif self.kind is ProviderContinuationKind.OPENAI_RESPONSE_ID:
            if not self.provider_response_id:
                raise ValueError("OpenAI response-id continuation requires a response ID")
        return self


class ReasoningConfiguration(ContractModel):
    """Control whether model reasoning is enabled and optionally select its strength."""

    enabled: bool = True
    effort: ReasoningEffort | None = None

    @model_validator(mode="after")
    def validate_disabled_reasoning_has_no_effort(self) -> "ReasoningConfiguration":
        """Reject an effort value that cannot take effect while reasoning is disabled."""

        if not self.enabled and self.effort is not None:
            raise ValueError("reasoning effort is only valid when reasoning is enabled")
        return self


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
    reasoning: ReasoningConfiguration | None = None
    temperature: float | None = Field(default=None, ge=0, le=2)
    max_output_tokens: int | None = Field(
        default=None,
        ge=1,
        le=MAX_PROVIDER_OUTPUT_TOKENS,
    )
    timeout_seconds: float = Field(default=60, ge=1, le=600)
    metadata: dict[str, str] = Field(default_factory=dict)
    provider_continuation_state: ProviderContinuationState | None = Field(
        default=None,
        exclude=True,
    )


class ModelResponse(ContractModel):
    """Return normalized content, accounting, diagnostics, and raw provider evidence."""

    text: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    structured_output: dict[str, Any] | list[Any] | None = None
    finish_reason: str
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    cached_tokens: int | None = Field(default=None, ge=0)
    reasoning_tokens: int | None = Field(default=None, ge=0)
    reasoning_blocks: list[ReasoningPresentation] = Field(default_factory=list)
    provider_continuation_state: ProviderContinuationState | None = Field(
        default=None,
        exclude=True,
    )
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
    REASONING_STARTED = "reasoning.started"
    REASONING_RAW_DELTA = "reasoning.raw.delta"
    REASONING_SUMMARY_DELTA = "reasoning.summary.delta"
    REASONING_COMPLETED = "reasoning.completed"
    REASONING_INTERRUPTED = "reasoning.interrupted"
    USAGE = "response.usage"
    COMPLETED = "response.completed"
    FAILED = "response.failed"


class StreamEvent(ContractModel):
    """Represent one ordered normalized stream event and its platform envelope."""

    type: StreamEventType
    sequence: int = Field(ge=1)
    schema_version: str = "conversation-stream.v2"
    event_id: str = Field(default_factory=lambda: str(uuid4()))
    response_id: str | None = None
    run_id: str | None = None
    turn_id: str | None = None
    step_id: str | None = None
    timestamp_ms: int | None = Field(default=None, ge=0)
    data: dict[str, Any] = Field(default_factory=dict)
    private_data: dict[str, Any] = Field(default_factory=dict, exclude=True)
