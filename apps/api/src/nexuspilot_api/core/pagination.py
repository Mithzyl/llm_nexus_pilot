"""Signed opaque cursor encoding for stable resource pagination."""

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import UTC, datetime

from nexuspilot_api.core.errors import InvalidCursorError


@dataclass(frozen=True)
class CursorPosition:
    """Identify the last row in a page using its stable sort fields."""

    created_at: datetime
    identifier: str


class CursorCodec:
    """Encode and verify opaque HMAC-signed cursor positions."""

    def __init__(self, signing_key: str) -> None:
        """Configure cursor signing with a non-empty server-side secret."""

        if not signing_key:
            raise ValueError("Cursor signing key must not be empty")
        self._key = signing_key.encode()

    def encode(self, position: CursorPosition) -> str:
        """Return a URL-safe signed cursor for one stable sort position."""

        created_at = position.created_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)
        payload = json.dumps(
            {
                "v": 1,
                "created_at": created_at.astimezone(UTC).isoformat(),
                "id": position.identifier,
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        encoded_payload = self._encode_bytes(payload)
        signature = hmac.new(self._key, encoded_payload.encode(), hashlib.sha256).digest()
        return f"{encoded_payload}.{self._encode_bytes(signature)}"

    def decode(self, cursor: str) -> CursorPosition:
        """Verify and decode a cursor or raise a stable invalid-cursor error."""

        try:
            encoded_payload, encoded_signature = cursor.split(".", 1)
            expected = hmac.new(
                self._key,
                encoded_payload.encode(),
                hashlib.sha256,
            ).digest()
            supplied = self._decode_bytes(encoded_signature)
            if not hmac.compare_digest(expected, supplied):
                raise InvalidCursorError
            payload = json.loads(self._decode_bytes(encoded_payload))
            if payload.get("v") != 1 or not payload.get("id"):
                raise InvalidCursorError
            created_at = datetime.fromisoformat(payload["created_at"])
            if created_at.tzinfo is None:
                raise InvalidCursorError
            return CursorPosition(
                created_at=created_at.astimezone(UTC),
                identifier=str(payload["id"]),
            )
        except InvalidCursorError:
            raise
        except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            raise InvalidCursorError from exc

    @staticmethod
    def _encode_bytes(value: bytes) -> str:
        """Encode bytes without URL-unsafe characters or padding."""

        return base64.urlsafe_b64encode(value).decode().rstrip("=")

    @staticmethod
    def _decode_bytes(value: str) -> bytes:
        """Decode URL-safe base64 after restoring required padding."""

        padding = "=" * (-len(value) % 4)
        return base64.urlsafe_b64decode(value + padding)
