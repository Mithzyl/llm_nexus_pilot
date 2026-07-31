"""Unit tests for signed opaque cursor encoding and validation."""

from datetime import UTC, datetime

import pytest

from nexuspilot_api.core.errors import InvalidCursorError
from nexuspilot_api.core.pagination import (
    CursorCodec,
    DatabasePaginationKey,
    DatabaseQueryPaginationKey,
    DatabaseSequencePaginationKey,
)


def test_cursor_round_trip_preserves_stable_position() -> None:
    """Verify a valid signed cursor restores its UTC timestamp and identifier."""

    codec = CursorCodec("test-signing-key")
    database_key = DatabasePaginationKey(
        created_at=datetime(2026, 7, 31, 12, 0, tzinfo=UTC),
        identifier="user-123",
    )

    decoded = codec.decode(codec.encode(database_key))

    assert decoded == database_key


def test_cursor_rejects_wrong_signing_key() -> None:
    """Verify cursors cannot be replayed across configurations with another key."""

    first = CursorCodec("first-signing-key")
    second = CursorCodec("second-signing-key")
    cursor = first.encode(
        DatabasePaginationKey(
            created_at=datetime(2026, 7, 31, 12, 0, tzinfo=UTC),
            identifier="user-123",
        )
    )

    with pytest.raises(InvalidCursorError):
        second.decode(cursor)


def test_sequence_cursor_round_trip_preserves_scope_and_sequence() -> None:
    """Verify sequence cursors retain both their database scope and page boundary."""

    codec = CursorCodec("test-signing-key")
    database_key = DatabaseSequencePaginationKey(
        scope_id="session-123",
        sequence=42,
    )

    decoded = codec.decode_sequence(codec.encode_sequence(database_key))

    assert decoded == database_key


def test_query_cursor_round_trip_preserves_database_key_and_filter_scope() -> None:
    """Verify query cursors retain their time boundary and normalized filter identity."""

    codec = CursorCodec("test-signing-key")
    database_key = DatabaseQueryPaginationKey(
        created_at=datetime(2026, 7, 31, 12, 0, tzinfo=UTC),
        identifier="run-123",
        query_fingerprint="a" * 64,
    )

    decoded = codec.decode_query(codec.encode_query(database_key))

    assert decoded == database_key


@pytest.mark.parametrize("cursor", ["", "missing-signature", "a.b.c", "%%%.%%%"])
def test_cursor_rejects_malformed_values(cursor: str) -> None:
    """Verify malformed cursor bytes consistently raise the public cursor error."""

    with pytest.raises(InvalidCursorError):
        CursorCodec("test-signing-key").decode(cursor)
