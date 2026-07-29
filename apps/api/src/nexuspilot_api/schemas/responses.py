"""HTTP schemas for the provider-neutral Responses facade."""

from typing import Any, Literal

from nexuspilot_models.contracts import (
    Message,
    MessageRole,
    ModelRequest,
    ProviderName,
    ToolCall,
    ToolDefinition,
)
from pydantic import BaseModel, Field, model_validator


class ResponsesRequest(BaseModel):
    """Accept an OpenAI-Responses-inspired request while retaining explicit provider routing."""

    run_id: str
    task_id: str | None = None
    provider: ProviderName
    model: str = Field(min_length=1, max_length=256)
    input: str | list[Message]
    instructions: str | None = Field(default=None, max_length=100_000)
    tools: list[ToolDefinition] = Field(default_factory=list, max_length=128)
    output_schema: dict[str, Any] | None = None
    temperature: float | None = Field(default=None, ge=0, le=2)
    max_output_tokens: int | None = Field(default=None, ge=1, le=1_000_000)
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
    usage: ResponseUsage
    latency_ms: int
    provider_request_id: str | None
