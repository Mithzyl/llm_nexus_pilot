"""Deterministic Memory policy, tokenization, and ranking tests."""

from decimal import Decimal

import pytest

from nexuspilot_api.core.errors import ResourceConflictError
from nexuspilot_api.features.memory.services.memory_policy import (
    MEMORY_TOKEN_ESTIMATOR_VERSION,
    build_memory_search_term_counts,
    calculate_memory_rank,
    estimate_memory_tokens,
    tokenize_memory_text,
    validate_memory_status_transition,
)
from nexuspilot_api.models import MemoryStatus


def test_memory_tokenizer_normalizes_latin_and_builds_chinese_bigrams() -> None:
    """Verify multilingual search terms remain deterministic across database engines."""

    first = tokenize_memory_text("  PYTHON   喜欢蓝色  ")
    second = tokenize_memory_text("python 喜欢蓝色")

    assert first == second
    assert "python" in first
    assert {"喜欢", "欢蓝", "蓝色"}.issubset(first)


def test_memory_status_transition_rejects_invalid_reactivation() -> None:
    """Verify terminal or replaced Memory facts cannot silently become active again."""

    validate_memory_status_transition(MemoryStatus.CANDIDATE, MemoryStatus.ACTIVE)
    validate_memory_status_transition(MemoryStatus.CANDIDATE, MemoryStatus.REJECTED)

    with pytest.raises(ResourceConflictError):
        validate_memory_status_transition(MemoryStatus.REJECTED, MemoryStatus.ACTIVE)
    with pytest.raises(ResourceConflictError):
        validate_memory_status_transition(MemoryStatus.SUPERSEDED, MemoryStatus.ACTIVE)


def test_memory_rank_is_deterministic_and_prefers_relevance() -> None:
    """Verify ranking records stable integer components and favors stronger lexical overlap."""

    relevant = calculate_memory_rank(
        matched_term_count=3,
        query_term_count=4,
        memory_term_count=5,
        scope_score=10_000,
        importance=Decimal("0.80"),
        confidence=Decimal("0.90"),
        age_days=3,
    )
    irrelevant = calculate_memory_rank(
        matched_term_count=0,
        query_term_count=4,
        memory_term_count=5,
        scope_score=10_000,
        importance=Decimal("1.00"),
        confidence=Decimal("1.00"),
        age_days=0,
    )

    assert relevant == calculate_memory_rank(
        matched_term_count=3,
        query_term_count=4,
        memory_term_count=5,
        scope_score=10_000,
        importance=Decimal("0.80"),
        confidence=Decimal("0.90"),
        age_days=3,
    )
    assert relevant.total_score > irrelevant.total_score
    assert relevant.lexical_score > 0


def test_memory_term_and_token_estimators_are_bounded_and_versioned() -> None:
    """Verify lexical growth is capped and token budgeting uses a conservative byte bound."""

    content_text = "中文 preference " + " ".join(f"term{index}" for index in range(400))
    term_counts = build_memory_search_term_counts(content_text)

    assert len(term_counts) == 256
    assert estimate_memory_tokens("中文") == len("中文".encode())
    assert MEMORY_TOKEN_ESTIMATOR_VERSION == "utf8_bytes_upper_bound_v1"
