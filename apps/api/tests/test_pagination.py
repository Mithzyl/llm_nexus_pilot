"""Unit tests for signed opaque cursor encoding and validation."""

from datetime import UTC, datetime

import pytest

from nexuspilot_api.core.errors import InvalidCursorError
from nexuspilot_api.core.pagination import CursorCodec, CursorPosition


def test_cursor_round_trip_preserves_stable_position() -> None:
    """Verify a valid signed cursor restores its UTC timestamp and identifier."""

    codec = CursorCodec("test-signing-key")
    position = CursorPosition(
        created_at=datetime(2026, 7, 31, 12, 0, tzinfo=UTC),
        identifier="user-123",
    )

    decoded = codec.decode(codec.encode(position))

    assert decoded == position


def test_cursor_rejects_wrong_signing_key() -> None:
    """Verify cursors cannot be replayed across configurations with another key."""

    first = CursorCodec("first-signing-key")
    second = CursorCodec("second-signing-key")
    cursor = first.encode(
        CursorPosition(
            created_at=datetime(2026, 7, 31, 12, 0, tzinfo=UTC),
            identifier="user-123",
        )
    )

    with pytest.raises(InvalidCursorError):
        second.decode(cursor)


@pytest.mark.parametrize("cursor", ["", "missing-signature", "a.b.c", "%%%.%%%"])
def test_cursor_rejects_malformed_values(cursor: str) -> None:
    """Verify malformed cursor bytes consistently raise the public cursor error."""

    with pytest.raises(InvalidCursorError):
        CursorCodec("test-signing-key").decode(cursor)
