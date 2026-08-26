"""Deterministic credential and high-sensitivity input screening policy."""

import hashlib
import re

from nexuspilot_api.core.errors import InvalidRequestError

CREDENTIAL_GUARD_VERSION = "credential_guard_v1"

_CREDENTIAL_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "aws_access_key",
        re.compile(
            r"\b(?:AKIA|ASIA|AIDA|AROA)[0-9A-Z]{16}\b",
            re.IGNORECASE,
        ),
    ),
    (
        "private_key_block",
        re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
    ),
    (
        "github_token",
        re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,255}\b"),
    ),
    (
        "slack_token",
        re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    ),
    (
        "stripe_secret_key",
        re.compile(r"\bsk_live_[0-9A-Za-z]{16,}\b"),
    ),
    (
        "jwt_bearer",
        re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
    ),
    (
        "generic_password_assignment",
        re.compile(
            r"\b(?:password|passwd|secret|api[_-]?key|access[_-]?token)\s*[=:]\s*\S+",
            re.IGNORECASE,
        ),
    ),
    (
        "basic_auth_header",
        re.compile(r"\bAuthorization\s*:\s*Basic\s+[A-Za-z0-9+/=]{16,}\b"),
    ),
)


def scan_for_sensitive_input(content_text: str) -> list[str]:
    """Return matched credential rule names without copying the offending text."""

    matched_rules: list[str] = []
    for rule_name, pattern in _CREDENTIAL_PATTERNS:
        if pattern.search(content_text):
            matched_rules.append(rule_name)
    return matched_rules


def reject_sensitive_memory_content(content_text: str) -> None:
    """Reject Memory content that embeds credentials before any fact is stored."""

    matched_rules = scan_for_sensitive_input(content_text)
    if not matched_rules:
        return
    input_hash = hashlib.sha256(content_text.encode()).hexdigest()[:16]
    raise InvalidRequestError(
        f"Memory content rejected by {CREDENTIAL_GUARD_VERSION} "
        f"rules={','.join(sorted(matched_rules))} input_hash={input_hash}"
    )
