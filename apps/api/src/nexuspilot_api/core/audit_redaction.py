"""Bound and redact JSON values before exposing internal audit records."""

import json
from typing import Any

_SENSITIVE_KEYS = frozenset(
    {
        "api_key",
        "authorization",
        "cookie",
        "password",
        "secret",
        "secret_key",
        "access_key",
        "private_key",
        "refresh_token",
        "access_token",
    }
)
_REDACTED = "[REDACTED]"
_MAX_DEPTH = 8
_MAX_COLLECTION_ITEMS = 100
_MAX_STRING_LENGTH = 2_000
_MAX_SERIALIZED_LENGTH = 16_000


def redact_audit_json(value: dict[str, Any]) -> dict[str, Any]:
    """Return bounded JSON with credential-shaped keys replaced by redaction markers."""

    redacted = _redact_value(value, depth=0)
    if not isinstance(redacted, dict):
        return {"value": redacted}
    serialized = json.dumps(
        redacted,
        ensure_ascii=False,
        default=str,
        separators=(",", ":"),
    )
    if len(serialized) <= _MAX_SERIALIZED_LENGTH:
        return redacted
    return {
        "_truncated": True,
        "preview": serialized[:_MAX_SERIALIZED_LENGTH],
    }


def _redact_value(value: Any, *, depth: int) -> Any:
    """Recursively redact one audit value while bounding depth and collection growth."""

    if depth >= _MAX_DEPTH:
        return "[DEPTH_LIMIT]"
    if isinstance(value, dict):
        redacted_mapping: dict[str, Any] = {}
        entries = list(value.items())
        for raw_key, nested_value in entries[:_MAX_COLLECTION_ITEMS]:
            key = str(raw_key)
            redacted_mapping[key] = (
                _REDACTED
                if _is_sensitive_key(key)
                else _redact_value(nested_value, depth=depth + 1)
            )
        if len(entries) > _MAX_COLLECTION_ITEMS:
            redacted_mapping["_truncated_items"] = len(entries) - _MAX_COLLECTION_ITEMS
        return redacted_mapping
    if isinstance(value, list):
        redacted_list = [
            _redact_value(item, depth=depth + 1)
            for item in value[:_MAX_COLLECTION_ITEMS]
        ]
        if len(value) > _MAX_COLLECTION_ITEMS:
            redacted_list.append(f"[TRUNCATED_{len(value) - _MAX_COLLECTION_ITEMS}_ITEMS]")
        return redacted_list
    if isinstance(value, str) and len(value) > _MAX_STRING_LENGTH:
        return f"{value[:_MAX_STRING_LENGTH]}…"
    return value


def _is_sensitive_key(key: str) -> bool:
    """Recognize exact and conventional suffix forms used for credentials."""

    normalized = key.strip().lower().replace("-", "_").replace(".", "_")
    return normalized in _SENSITIVE_KEYS or normalized.endswith(
        ("_password", "_secret", "_token", "_api_key", "_private_key")
    )
