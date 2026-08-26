"""HTTP schemas for the provider-neutral Responses facade."""

from typing import Any, Literal

from nexuspilot_models.contracts import (
    MAX_PROVIDER_OUTPUT_TOKENS,
    Message,
    MessageRole,
    ModelRequest,
    ProviderName,
    ReasoningConfiguration,
    ReasoningDisplayPolicy,
    ReasoningPresentation,
    ToolCall,
    ToolDefinition,
)
from pydantic import BaseModel, ConfigDict, Field, model_validator


class ResponsesRequest(BaseModel):
    """Accept an OpenAI-Responses-inspired request while retaining explicit provider routing."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    current_user_message_id: str | None = Field(default=None, min_length=1, max_length=36)
    append_input_to_context: bool = False
    task_id: str | None = None
    provider: ProviderName
    model: str = Field(min_length=1, max_length=128)
    input: str | list[Message]
    instructions: str | None = Field(default=None, max_length=100_000)
    tools: list[ToolDefinition] = Field(default_factory=list, max_length=128)
    output_schema: dict[str, Any] | None = None
    json_object_output: bool = False
    reasoning: ReasoningConfiguration | None = None
    reasoning_display_policy: ReasoningDisplayPolicy = ReasoningDisplayPolicy.HIDDEN
    continuation_from_attempt_id: str | None = Field(default=None, min_length=1, max_length=36)
    temperature: float | None = Field(default=None, ge=0, le=2)
    max_output_tokens: int | None = Field(
        default=None,
        ge=1,
        le=MAX_PROVIDER_OUTPUT_TOKENS,
    )
    timeout_seconds: float = Field(default=60, ge=1, le=600)
    metadata: dict[str, str] = Field(default_factory=dict)
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=128)
    stream: bool = False

    @model_validator(mode="after")
    def validate_input(self) -> "ResponsesRequest":
        """Reject empty string and empty-list inputs before a provider attempt is created."""

        if isinstance(self.input, str) and not self.input.strip():
            raise ValueError("input cannot be empty")
        if isinstance(self.input, list) and not self.input:
            raise ValueError("input cannot be empty")
        if self.json_object_output and self.output_schema is not None:
            raise ValueError("json_object_output cannot be combined with output_schema")
        return self

    def to_model_request(self) -> ModelRequest:
        """Convert the HTTP facade request into the provider-neutral domain contract."""

        messages = (
            [Message(role=MessageRole.USER, content=self.input)]
            if isinstance(self.input, str)
            else self.input
        )
        return ModelRequest(
            provider=self.provider,
            model=self.model,
            messages=messages,
            system_instruction=self.instructions,
            tools=self.tools,
            output_schema=self.output_schema,
            json_object_output=self.json_object_output,
            reasoning=self.reasoning,
            temperature=self.temperature,
            max_output_tokens=self.max_output_tokens,
            timeout_seconds=self.timeout_seconds,
            metadata=self.metadata,
        )


class ResponseUsage(BaseModel):
    """Expose normalized token counts and an optional explicit-price estimate."""

    input_tokens: int | None
    output_tokens: int | None
    cached_tokens: int | None
    reasoning_tokens: int | None
    estimated_cost: str | None


class ResponsesResult(BaseModel):
    """Return one completed response without exposing the full native provider payload."""

    id: str
    object: Literal["response"] = "response"
    status: Literal["completed"] = "completed"
    provider: ProviderName
    model: str
    output_text: str | None
    tool_calls: list[ToolCall]
    structured_output: dict[str, Any] | list[Any] | None
    finish_reason: str
    reasoning_blocks: list[ReasoningPresentation]
    usage: ResponseUsage
    latency_ms: int
    provider_request_id: str | None
