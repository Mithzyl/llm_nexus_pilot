"""Pure Memory normalization, lifecycle, scope, and ranking policy."""

import hashlib
import json
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from nexuspilot_api.core.errors import InvalidRequestError, ResourceConflictError
from nexuspilot_api.models import (
    MemorySourceType,
    MemoryStatus,
    MemoryTrustLevel,
    MemoryType,
)

MEMORY_TOKEN_ESTIMATOR_VERSION = "utf8_bytes_upper_bound_v1"
MEMORY_CANDIDATE_METHOD = "lexical_hash_v1"
MEMORY_RANKER_VERSION = "memory_ranker_v1"
MAX_MEMORY_SEARCH_TERMS = 256
MAX_MEMORY_QUERY_TERMS = 64

PROJECT_MEMORY_TYPES = frozenset(
    {
        MemoryType.PROJECT_FACT,
        MemoryType.PROJECT_DECISION,
        MemoryType.PROJECT_RULE,
        MemoryType.PROJECT_CONVENTION,
        MemoryType.PROJECT_ENVIRONMENT,
    }
)

SOURCE_TRUST_LEVELS: dict[MemorySourceType, MemoryTrustLevel] = {
    MemorySourceType.MESSAGE: MemoryTrustLevel.DIRECT_USER_STATEMENT,
    MemorySourceType.MODEL_ATTEMPT: MemoryTrustLevel.MODEL_INFERENCE,
    MemorySourceType.ARTIFACT: MemoryTrustLevel.EXTERNAL_UNTRUSTED,
    MemorySourceType.TOOL_CALL: MemoryTrustLevel.EXTERNAL_UNTRUSTED,
    MemorySourceType.TRUSTED_REQUEST: MemoryTrustLevel.INTERNAL_SYSTEM_RESULT,
}

_LATIN_TOKEN_PATTERN = re.compile(r"[a-z0-9]+(?:[._-][a-z0-9]+)*")
_CJK_SEQUENCE_PATTERN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+")


@dataclass(frozen=True)
class MemoryRankComponents:
    """Contain deterministic basis-point components and the final weighted score."""

    total_score: int
    lexical_score: int
    scope_score: int
    importance_score: int
    confidence_score: int
    recency_score: int


def normalize_memory_text(content_text: str) -> str:
    """Normalize Unicode, case, and whitespace for deterministic hashes and search."""

    normalized = unicodedata.normalize("NFKC", content_text).casefold()
    return " ".join(normalized.split())


def tokenize_memory_text(content_text: str) -> tuple[str, ...]:
    """Return ordered Latin tokens and overlapping Chinese bigrams for Memory search."""

    normalized = normalize_memory_text(content_text)
    tokens: list[str] = _LATIN_TOKEN_PATTERN.findall(normalized)
    for cjk_sequence in _CJK_SEQUENCE_PATTERN.findall(normalized):
        if len(cjk_sequence) == 1:
            tokens.append(cjk_sequence)
        else:
            tokens.extend(cjk_sequence[index : index + 2] for index in range(len(cjk_sequence) - 1))
    return tuple(tokens)


def build_memory_search_term_counts(
    content_text: str,
    *,
    maximum_unique_terms: int = MAX_MEMORY_SEARCH_TERMS,
) -> dict[str, int]:
    """Hash bounded lexical terms and retain frequencies for a database-neutral index."""

    frequencies = Counter(tokenize_memory_text(content_text))
    selected_terms = sorted(
        frequencies.items(),
        key=lambda item: (-item[1], item[0]),
    )[:maximum_unique_terms]
    return {
        hashlib.sha256(term.encode()).hexdigest(): frequency for term, frequency in selected_terms
    }


def hash_memory_content(content_text: str) -> tuple[str, str]:
    """Return raw and normalized SHA-256 hashes without exposing Memory text."""

    raw_hash = hashlib.sha256(content_text.encode()).hexdigest()
    normalized_hash = hashlib.sha256(normalize_memory_text(content_text).encode()).hexdigest()
    return raw_hash, normalized_hash


def hash_memory_request(payload: dict[str, Any]) -> str:
    """Hash a canonical JSON-compatible request for durable idempotency checks."""

    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        default=_json_default,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def hash_active_semantic_key(
    *,
    user_id: str,
    session_id: str | None,
    run_id: str | None,
    task_id: str | None,
    project_id: str | None = None,
    memory_type: MemoryType,
    semantic_key: str | None,
) -> str | None:
    """Build a fixed-width uniqueness key for an active semantic Memory slot."""

    if semantic_key is None:
        return None
    scope = "|".join(
        [
            user_id,
            project_id or "",
            session_id or "",
            run_id or "",
            task_id or "",
            memory_type.value,
            normalize_memory_text(semantic_key),
        ]
    )
    return hashlib.sha256(scope.encode()).hexdigest()


def estimate_memory_tokens(content_text: str) -> int:
    """Return a provider-neutral upper bound that budgets at most one token per UTF-8 byte."""

    return max(1, len(content_text.encode()))


def validate_memory_status_transition(
    current_status: MemoryStatus,
    target_status: MemoryStatus,
) -> None:
    """Reject lifecycle transitions that would revive or rewrite terminal Memory facts."""

    if current_status == target_status:
        return
    allowed_targets = {
        MemoryStatus.CANDIDATE: {MemoryStatus.ACTIVE, MemoryStatus.REJECTED},
        MemoryStatus.ACTIVE: {MemoryStatus.SUPERSEDED},
    }
    if target_status not in allowed_targets.get(current_status, set()):
        raise ResourceConflictError(
            f"Cannot change Memory from {current_status.value} to {target_status.value}"
        )


def validate_memory_type_scope(
    *,
    memory_type: MemoryType,
    session_id: str | None,
    run_id: str | None,
    task_id: str | None,
    expires_at: datetime | None,
    project_id: str | None = None,
) -> None:
    """Enforce the minimum durable scope required by each Memory type."""

    if memory_type in PROJECT_MEMORY_TYPES and project_id is None:
        raise InvalidRequestError("Project Memory requires an explicit project_id")
    if memory_type not in PROJECT_MEMORY_TYPES and project_id is not None:
        raise InvalidRequestError("Only project Memory types may bind a project_id")
    if memory_type == MemoryType.WORKING_CONTEXT:
        if not any((session_id, run_id, task_id)):
            raise InvalidRequestError("Working-context Memory requires a scoped resource")
        if expires_at is None:
            raise InvalidRequestError("Working-context Memory requires expires_at")
    if memory_type == MemoryType.SESSION_EPISODE and session_id is None:
        raise InvalidRequestError("Session-episode Memory requires session_id")
    if memory_type == MemoryType.EXECUTION_LESSON and run_id is None and task_id is None:
        raise InvalidRequestError("Execution-lesson Memory requires run_id or task_id")


def source_trust_level(
    source_type: MemorySourceType,
    explicit_level: MemoryTrustLevel | None,
) -> MemoryTrustLevel:
    """Assign the receiving-edge trust boundary without trusting model self-reports."""

    if explicit_level is None:
        return SOURCE_TRUST_LEVELS[source_type]
    allowed_levels = _allowed_explicit_trust_levels(source_type)
    if explicit_level not in allowed_levels:
        raise InvalidRequestError(
            f"Trust level {explicit_level.value} is not allowed for {source_type.value} sources"
        )
    return explicit_level


def _allowed_explicit_trust_levels(source_type: MemorySourceType) -> frozenset[MemoryTrustLevel]:
    """Return the trust boundaries a caller may declare for each receiving edge."""

    if source_type == MemorySourceType.MESSAGE:
        return frozenset(
            {
                MemoryTrustLevel.DIRECT_USER_STATEMENT,
                MemoryTrustLevel.USER_CONFIRMED,
                MemoryTrustLevel.EXTERNAL_UNTRUSTED,
            }
        )
    if source_type == MemorySourceType.TRUSTED_REQUEST:
        return frozenset({MemoryTrustLevel.INTERNAL_SYSTEM_RESULT})
    if source_type == MemorySourceType.MODEL_ATTEMPT:
        return frozenset({MemoryTrustLevel.MODEL_INFERENCE})
    return frozenset({MemoryTrustLevel.EXTERNAL_UNTRUSTED})


def calculate_memory_rank(
    *,
    matched_term_count: int,
    query_term_count: int,
    memory_term_count: int,
    scope_score: int,
    importance: Decimal,
    confidence: Decimal,
    age_days: int,
) -> MemoryRankComponents:
    """Calculate a reproducible weighted Memory rank using integer basis points."""

    union_count = query_term_count + memory_term_count - matched_term_count
    lexical_raw = 0 if union_count <= 0 else matched_term_count * 10_000 // union_count
    importance_raw = int(importance * 10_000)
    confidence_raw = int(confidence * 10_000)
    recency_raw = 300_000 // (30 + max(0, age_days))
    lexical_component = lexical_raw * 60 // 100
    scope_component = _bounded_basis_points(scope_score) * 15 // 100
    importance_component = _bounded_basis_points(importance_raw) * 10 // 100
    confidence_component = _bounded_basis_points(confidence_raw) * 10 // 100
    recency_component = _bounded_basis_points(recency_raw) * 5 // 100
    return MemoryRankComponents(
        total_score=(
            lexical_component
            + scope_component
            + importance_component
            + confidence_component
            + recency_component
        ),
        lexical_score=lexical_raw,
        scope_score=_bounded_basis_points(scope_score),
        importance_score=importance_raw,
        confidence_score=confidence_raw,
        recency_score=_bounded_basis_points(recency_raw),
    )


def _bounded_basis_points(value: int) -> int:
    """Clamp an integer score to the public zero-to-ten-thousand range."""

    return max(0, min(10_000, value))


def _json_default(value: object) -> str:
    """Serialize enums, Decimal values, and datetimes for canonical request hashing."""

    if hasattr(value, "value"):
        return str(value.value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    raise TypeError(f"Unsupported request hash value: {type(value).__name__}")
