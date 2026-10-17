"""Allowlisted diagnostics shared by HTTP failures and in-band provider errors."""

import hashlib
from typing import Any


def provider_error_diagnostic(payload: Any, http_status: int | None = None) -> dict:
    """Classify errors without storing arbitrary messages, request echoes or credentials."""
    error = payload.get("error") if isinstance(payload, dict) else payload
    fields = error if isinstance(error, dict) else {}
    message = fields.get("message", error if isinstance(error, str) else "")
    message = message if isinstance(message, str) else ""
    categories = {
        "context_length_exceeded",
        "invalid_api_key",
        "rate_limit_exceeded",
        "insufficient_quota",
        "model_not_found",
        "invalid_request_error",
        "unsupported_parameter",
        "server_error",
        "invalid_json_schema",
    }
    code = fields.get("code")
    category = code if isinstance(code, str) and code in categories else "unknown"
    if category == "unknown":
        text = message[:8192].lower()
        for phrase, value in (
            ("context length", "context_length_exceeded"),
            ("rate limit", "rate_limit_exceeded"),
            ("insufficient quota", "insufficient_quota"),
            ("invalid api key", "invalid_api_key"),
            ("unsupported parameter", "unsupported_parameter"),
            ("grammar", "structured_output_error"),
        ):
            if phrase in text:
                category = value
                break
    param = fields.get("param")
    diagnostic = {
        "category": category,
        "category_source": "code"
        if category == code
        else ("message_classification" if category != "unknown" else "unclassified"),
        "message_sha256": hashlib.sha256(message.encode()).hexdigest(),
        "message_length": len(message),
        "param": param
        if isinstance(param, str)
        and param
        in {
            "model",
            "messages",
            "max_tokens",
            "max_completion_tokens",
            "response_format",
            "tools",
            "stream",
            "temperature",
        }
        else None,
    }
    return {
        "record_kind": "protocol_diagnostic",
        "error_envelope": isinstance(payload, dict)
        and payload.get("error") is not None,
        "http_status": http_status,
        "provider_error": diagnostic,
    }
