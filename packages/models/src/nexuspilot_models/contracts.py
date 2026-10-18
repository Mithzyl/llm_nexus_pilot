"""Stable provider-neutral request, response, and streaming contracts."""

import base64
import binascii
import hashlib
from enum import StrEnum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, model_validator

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
    STRATA = "strata"


class MessageRole(StrEnum):
    """Represent conversation roles supported by the current text and tool contract."""

    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class ReasoningEffort(StrEnum):
    """Identify the portable reasoning-strength levels supported by the public contract."""

    MINIMAL = "minimal"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    XHIGH = "xhigh"
    MAX = "max"


class ReasoningConfigurationSource(StrEnum):
    """Identify whether one effective reasoning choice came from the caller or model policy."""

    MODEL_DEFAULT = "model_default"
    REQUEST = "request"


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
    ANTHROPIC_CONTENT = "anthropic.signed-content.v1"
    GEMINI_PARTS = "gemini.signed-parts.v1"


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
    signed_content_blocks: list[dict[str, Any]] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_kind_payload(self) -> "ProviderContinuationState":
        """Reject continuation records whose payload does not match the declared kind."""

        if self.kind is ProviderContinuationKind.DEEPSEEK_RAW_REASONING:
            if not self.raw_reasoning_for_tool_continuation:
                raise ValueError("DeepSeek continuation requires raw reasoning")
        elif self.kind is ProviderContinuationKind.OPENAI_ENCRYPTED_ITEMS:
            if not self.encrypted_reasoning_items:
                raise ValueError(
                    "OpenAI encrypted-item continuation requires output items"
                )
        elif self.kind is ProviderContinuationKind.OPENAI_RESPONSE_ID:
            if not self.provider_response_id:
                raise ValueError(
                    "OpenAI response-id continuation requires a response ID"
                )
        elif self.kind in {
            ProviderContinuationKind.ANTHROPIC_CONTENT,
            ProviderContinuationKind.GEMINI_PARTS,
        }:
            if not self.signed_content_blocks:
                raise ValueError(
                    "Signed continuation requires unchanged content blocks"
                )
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


class ResolvedReasoningConfiguration(ContractModel):
    """Carry an audited portable choice and its model-specific provider value internally."""

    source: ReasoningConfigurationSource
    profile_version: str = Field(min_length=1, max_length=64)
    provider_value: str | int | None = None

    @model_validator(mode="after")
    def validate_provider_value(self) -> "ResolvedReasoningConfiguration":
        """Reject empty strings and booleans that cannot be valid provider effort values."""

        if isinstance(self.provider_value, bool):
            raise ValueError("provider reasoning value cannot be boolean")
        if isinstance(self.provider_value, str) and not self.provider_value.strip():
            raise ValueError("provider reasoning value cannot be empty")
        return self


NativeMediaType = Literal[
    "application/pdf", "image/png", "image/jpeg", "image/webp", "image/gif"
]
MAX_NATIVE_FILE_BYTES = 20 * 1024 * 1024
MAX_NATIVE_REQUEST_BYTES = 20 * 1024 * 1024
NATIVE_MEDIA_TYPES_BY_PROVIDER: dict[str, frozenset[str]] = {
    "openai": frozenset(
        {"application/pdf", "image/png", "image/jpeg", "image/webp", "image/gif"}
    ),
    "anthropic": frozenset(
        {"application/pdf", "image/png", "image/jpeg", "image/webp", "image/gif"}
    ),
    "gemini": frozenset({"application/pdf", "image/png", "image/jpeg", "image/webp"}),
}


class NativeInputCapabilities(ContractModel):
    """Pin an exact model's admitted MIME types and deployment bounds, not inferred vision support."""

    media_types: list[NativeMediaType] = Field(max_length=5)
    max_file_bytes: int = Field(gt=0, le=MAX_NATIVE_FILE_BYTES)
    max_request_bytes: int = Field(gt=0, le=MAX_NATIVE_REQUEST_BYTES)
    max_files: int = Field(gt=0, le=10)
    input_token_reserve_per_file: int = Field(gt=0, le=200_000)

    @model_validator(mode="after")
    def validate_native_input_policy(self) -> "NativeInputCapabilities":
        """Reject duplicate formats and impossible file/request resource policies."""

        if len(self.media_types) != len(set(self.media_types)):
            raise ValueError("native media types must be unique")
        if self.max_file_bytes > self.max_request_bytes:
            raise ValueError("native file limit exceeds request limit")
        return self


class NativeFileContent(ContractModel):
    """Carry verified internal bytes; URLs, paths and supplier file IDs are intentionally absent."""

    filename: str = Field(min_length=1, max_length=255)
    media_type: NativeMediaType
    data_base64: str = Field(
        min_length=1, max_length=4 * ((MAX_NATIVE_FILE_BYTES + 2) // 3), repr=False
    )
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def validate_native_file_content(self) -> "NativeFileContent":
        """Verify bounded Base64, digest and signature; deep document decoding belongs to the provider."""

        if (
            any(character in self.filename for character in ("/", "\\"))
            or any(ord(character) < 32 for character in self.filename)
            or self.filename in {".", ".."}
        ):
            raise ValueError(
                "native filename must be a display name without paths or controls"
            )
        try:
            content = base64.b64decode(self.data_base64, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError("native content must be valid Base64") from exc
        if not content or len(content) > MAX_NATIVE_FILE_BYTES:
            raise ValueError("native file size is outside supported bounds")
        if base64.b64encode(content).decode("ascii") != self.data_base64:
            raise ValueError("native content must use canonical Base64")
        if hashlib.sha256(content).hexdigest() != self.content_hash:
            raise ValueError("native content hash mismatch")
        signatures = {
            "application/pdf": content.startswith(b"%PDF-"),
            "image/png": content.startswith(b"\x89PNG\r\n\x1a\n"),
            "image/jpeg": content.startswith(b"\xff\xd8\xff"),
            "image/gif": content.startswith((b"GIF87a", b"GIF89a")),
            "image/webp": content.startswith(b"RIFF") and content[8:12] == b"WEBP",
        }
        if not signatures[self.media_type]:
            raise ValueError("native content signature does not match media type")
        return self

    @property
    def size_bytes(self) -> int:
        """Return decoded size without allocating another copy of the verified bytes."""

        padding_count = len(self.data_base64) - len(self.data_base64.rstrip("="))
        return len(self.data_base64) * 3 // 4 - padding_count


class ToolCall(ContractModel):
    """Represent one normalized function request emitted by a model."""

    id: str
    name: str
    arguments: dict[str, Any]


class Message(ContractModel):
    """Represent text with optional verified user attachments, independent of provider wire blocks."""

    role: MessageRole
    content: str
    tool_calls: list[ToolCall] = Field(default_factory=list, max_length=128)
    _provider_continuation_state: ProviderContinuationState | None = PrivateAttr(
        default=None
    )
    attachments: list[NativeFileContent] = Field(default_factory=list, max_length=10)
    tool_call_id: str | None = Field(default=None, max_length=256)
    tool_name: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def validate_message_content(self) -> "Message":
        """Validate complete assistant calls and results; private state is never public input."""

        if self.tool_calls and self.role is not MessageRole.ASSISTANT:
            raise ValueError("tool_calls are only valid on assistant messages")
        if not self.content and not self.tool_calls:
            raise ValueError("Message requires text or assistant tool calls")
        if self.attachments and self.role is not MessageRole.USER:
            raise ValueError("native attachments are allowed only on user messages")
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


class TransportAttempt(ContractModel):
    """Capture one physical HTTP attempt for persistence by the calling application."""

    attempt_index: int = Field(ge=1)
    status_code: int | None = None
    latency_ms: int = Field(ge=0)
    error_type: str | None = None
    error_message: str | None = None


class ModelRequest(ContractModel):
    """Define one provider-neutral model generation request."""

    # Internal workflow policy, never accepted as an HTTP or provider payload field.
    _defer_structured_validation: bool = PrivateAttr(default=False)

    provider: ProviderName
    model: str = Field(min_length=1, max_length=256)
    messages: list[Message] = Field(min_length=1, max_length=1000)
    native_input_capabilities: NativeInputCapabilities | None = Field(
        default=None, exclude=True
    )
    system_instruction: str | None = Field(default=None, max_length=100_000)
    tools: list[ToolDefinition] = Field(default_factory=list, max_length=128)
    output_schema: dict[str, Any] | None = None
    json_object_output: bool = False
    reasoning: ReasoningConfiguration | None = None
    resolved_reasoning: ResolvedReasoningConfiguration | None = Field(
        default=None,
        exclude=True,
    )
    temperature: float | None = Field(default=None, ge=0, le=2)
    max_output_tokens: int | None = Field(
        default=None,
        ge=1,
        le=MAX_PROVIDER_OUTPUT_TOKENS,
    )
    timeout_seconds: float | None = Field(
        default=60, ge=1, le=600,
        description="Null disables the generation deadline, not explicit cancellation.",
    )
    metadata: dict[str, str] = Field(default_factory=dict)
    provider_continuation_state: ProviderContinuationState | None = Field(
        default=None,
        exclude=True,
    )

    @model_validator(mode="after")
    def validate_generation_contract(self) -> "ModelRequest":
        """Validate output exclusivity and fail closed for unknown or exceeded native input policy."""

        attachments = [
            attachment
            for message in self.messages
            for attachment in message.attachments
        ]
        if attachments:
            policy = self.native_input_capabilities
            if policy is None:
                raise ValueError(
                    "native inputs require exact-model capability evidence"
                )
            if (
                len(attachments) > policy.max_files
                or sum(attachment.size_bytes for attachment in attachments)
                > policy.max_request_bytes
            ):
                raise ValueError("native request exceeds configured limits")
            if any(
                attachment.media_type not in policy.media_types
                or attachment.size_bytes > policy.max_file_bytes
                for attachment in attachments
            ):
                raise ValueError(
                    "native file type or size is not supported by this model"
                )
        if self.json_object_output and self.output_schema is not None:
            raise ValueError("json_object_output cannot be combined with output_schema")
        return self


class ModelResponse(ContractModel):
    """Return normalized content, accounting, diagnostics, and raw provider evidence."""

    text: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    structured_output: dict[str, Any] | list[Any] | None = None
    finish_reason: str
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    cached_tokens: int | None = Field(default=None, ge=0)
    cache_write_tokens: int | None = Field(default=None, ge=0)
    cache_miss_tokens: int | None = Field(default=None, ge=0)
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
