"""Shared parsing helpers that do not define provider wire formats."""

import json
from typing import Any

from nexuspilot_models.errors import ModelProviderError


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
