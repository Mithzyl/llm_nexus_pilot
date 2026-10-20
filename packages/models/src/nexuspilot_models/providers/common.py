"""Shared parsing helpers that do not define provider wire formats."""

import asyncio
import json
import time
from functools import wraps
from typing import Any

from nexuspilot_models.contracts import (
    NATIVE_MEDIA_TYPES_BY_PROVIDER,
    Message,
    ModelRequest,
    ModelResponse,
    ProviderName,
    StreamEventType,
)
from nexuspilot_models.errors import ModelProviderError
from nexuspilot_models.stream_progress import (
    ModelStreamProgress,
    interrupted_stream_progress,
)


def preserve_stream_usage(stream):
    """Retain received draft and usage on failure/cancel, never inventing completed output."""

    @wraps(stream)
    async def tracked(*args, **kwargs):
        """Copy known nonnegative counters; repeated snapshots replace, never add."""
        usage = {}
        progress = ModelStreamProgress()
        started = time.perf_counter()
        fields = {
            "input_tokens",
            "output_tokens",
            "cached_tokens",
            "cache_write_tokens",
            "cache_miss_tokens",
            "reasoning_tokens",
        }
        try:
            async for event in stream(*args, **kwargs):
                progress.record(event)
                if event.type == StreamEventType.USAGE:
                    for key in fields:
                        value = event.data.get(key)
                        if (
                            isinstance(value, int)
                            and not isinstance(value, bool)
                            and value >= 0
                        ):
                            usage[key] = value
                yield event
        except asyncio.CancelledError as error:
            # asyncio.timeout on Python 3.11 checks the exact CancelledError
            # type. Keep the original exception, not a subclass or replacement.
            error.stream_progress = interrupted_stream_progress(error) or progress.snapshot()
            raise
        except ModelProviderError as error:
            error.stream_progress = interrupted_stream_progress(error) or progress.snapshot()
            if usage and error.response is None:
                error.response = ModelResponse(
                    **usage,
                    finish_reason="error",
                    latency_ms=int((time.perf_counter() - started) * 1000),
                    raw_response={
                        "record_kind": "interrupted_stream_usage",
                        "usage_complete": False,
                        "usage": usage,
                        "diagnostic": error.raw_error,
                    },
                    transport_attempts=error.transport_attempts,
                    provider_request_id=error.provider_request_id,
                )
            if error.response is not None:
                error.response.raw_response["stream_progress"] = error.stream_progress
            else:
                error.raw_error["stream_progress"] = error.stream_progress
            raise

    return tracked


def validate_structured_response(
    response: ModelResponse, request: ModelRequest
) -> ModelResponse:
    """Validate completed JSON while retaining normalized billing evidence on failure.

    The response attached to an exception is backend-only evidence, not a successful
    public response. Workflow nodes may defer validation until after accounting.
    """
    if not (request.output_schema or request.json_object_output):
        return response
    try:
        response.structured_output = parse_structured_output(
            response.text, defer_validation=request._defer_structured_validation
        )
    except ModelProviderError as error:
        error.response = response
        error.raw_error = response.raw_response
        error.transport_attempts = response.transport_attempts
        error.provider_request_id = response.provider_request_id
        raise
    return response


def native_content_blocks(
    message: Message, provider: ProviderName
) -> list[dict[str, Any]]:
    """Encode verified bytes as native inline content; never resolve paths or fetch URLs."""

    blocks: list[dict[str, Any]] = [
        {"text": message.content}
        if provider is ProviderName.GEMINI
        else {
            "type": "input_text" if provider is ProviderName.OPENAI else "text",
            "text": message.content,
        }
    ]
    for attachment in message.attachments:
        if attachment.media_type not in NATIVE_MEDIA_TYPES_BY_PROVIDER.get(
            provider.value, frozenset()
        ):
            raise ModelProviderError(
                "unsupported_capability",
                "This codec does not support this native media type.",
            )
        is_pdf = attachment.media_type == "application/pdf"
        if provider is ProviderName.OPENAI:
            data_url = f"data:{attachment.media_type};base64,{attachment.data_base64}"
            blocks.append(
                {
                    "type": "input_file",
                    "filename": attachment.filename,
                    "file_data": data_url,
                }
                if is_pdf
                else {"type": "input_image", "image_url": data_url, "detail": "auto"}
            )
        elif provider is ProviderName.ANTHROPIC:
            blocks.append(
                {
                    "type": "document" if is_pdf else "image",
                    "source": {
                        "type": "base64",
                        "media_type": attachment.media_type,
                        "data": attachment.data_base64,
                    },
                }
            )
        elif provider is ProviderName.GEMINI:
            blocks.append(
                {
                    "inlineData": {
                        "mimeType": attachment.media_type,
                        "data": attachment.data_base64,
                    }
                }
            )
        else:
            raise ModelProviderError(
                "unsupported_capability",
                "This codec does not support native file input.",
            )
    return blocks


def parse_json_arguments(
    value: str | dict[str, Any], *, context: str
) -> dict[str, Any]:
    """Parse provider tool arguments and reject non-object or invalid JSON values."""

    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value or "{}")
    except json.JSONDecodeError as exc:
        raise ModelProviderError(
            "response_parse_error",
            f"Provider returned invalid JSON for {context}.",
        ) from exc
    if not isinstance(parsed, dict):
        raise ModelProviderError(
            "response_parse_error",
            f"Provider returned non-object JSON for {context}.",
        )
    return parsed


def parse_structured_output(
    text: str | None, *, defer_validation: bool = False
) -> dict[str, Any] | list[Any] | None:
    """Parse JSON; an internal workflow may retain raw text and validate it after accounting."""

    if text is None:
        return None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        if defer_validation:
            return None
        raise ModelProviderError(
            "response_parse_error",
            "Provider returned invalid structured JSON output.",
        ) from exc
    if not isinstance(parsed, (dict, list)):
        if defer_validation:
            return None
        raise ModelProviderError(
            "response_parse_error",
            "Provider returned a structured scalar instead of an object or array.",
        )
    return parsed
