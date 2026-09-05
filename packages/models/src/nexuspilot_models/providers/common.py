"""Shared parsing helpers that do not define provider wire formats."""

import json
from typing import Any

from nexuspilot_models.contracts import (
    NATIVE_MEDIA_TYPES_BY_PROVIDER,
    Message,
    ProviderName,
)
from nexuspilot_models.errors import ModelProviderError


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


def parse_structured_output(text: str | None) -> dict[str, Any] | list[Any] | None:
    """Parse a requested structured response and reject invalid JSON deterministically."""

    if text is None:
        return None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ModelProviderError(
            "response_parse_error",
            "Provider returned invalid structured JSON output.",
        ) from exc
    if not isinstance(parsed, (dict, list)):
        raise ModelProviderError(
            "response_parse_error",
            "Provider returned a structured scalar instead of an object or array.",
        )
    return parsed
