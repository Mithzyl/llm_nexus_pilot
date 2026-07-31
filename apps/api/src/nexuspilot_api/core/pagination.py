"""Signed opaque cursor encoding for stable resource pagination."""

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import UTC, datetime

from nexuspilot_api.core.errors import InvalidCursorError


@dataclass(frozen=True)
class DatabasePaginationKey:
    """Identify a database page boundary using the last row's stable sort fields."""

    created_at: datetime
    identifier: str


@dataclass(frozen=True)
class DatabaseSequencePaginationKey:
    """Identify a scoped database page boundary using a unique sequence number."""

    scope_id: str
    sequence: int


class CursorCodec:
    """Encode and verify opaque HMAC-signed cursor positions."""

    def __init__(self, signing_key: str) -> None:
        """Configure cursor signing with a non-empty server-side secret."""

        if not signing_key:
            raise ValueError("Cursor signing key must not be empty")
        self._key = signing_key.encode()

    def encode(self, database_key: DatabasePaginationKey) -> str:
        """Return a URL-safe cursor containing one signed database pagination key."""

        created_at = database_key.created_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)
        payload = json.dumps(
            {
                "v": 1,
                "created_at": created_at.astimezone(UTC).isoformat(),
                "id": database_key.identifier,
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        return self._sign_payload(payload)

    def decode(self, cursor: str) -> DatabasePaginationKey:
        """Verify a public cursor and return its internal database pagination key."""

        try:
            payload = self._verify_payload(cursor)
            if payload.get("v") != 1 or not payload.get("id"):
                raise InvalidCursorError
            created_at = datetime.fromisoformat(payload["created_at"])
            if created_at.tzinfo is None:
                raise InvalidCursorError
            return DatabasePaginationKey(
                created_at=created_at.astimezone(UTC),
                identifier=str(payload["id"]),
            )
        except InvalidCursorError:
            raise
        except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            raise InvalidCursorError from exc

    def encode_sequence(self, database_key: DatabaseSequencePaginationKey) -> str:
        """Return a signed cursor for a sequence-ordered database page boundary."""

        payload = json.dumps(
            {
                "v": 1,
                "scope_id": database_key.scope_id,
                "sequence": database_key.sequence,
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        return self._sign_payload(payload)

    def decode_sequence(self, cursor: str) -> DatabaseSequencePaginationKey:
        """Verify a sequence cursor and return its scoped database pagination key."""

        try:
            payload = self._verify_payload(cursor)
            if (
                payload.get("v") != 1
                or not payload.get("scope_id")
                or not isinstance(payload.get("sequence"), int)
                or payload["sequence"] < 1
            ):
                raise InvalidCursorError
            return DatabaseSequencePaginationKey(
                scope_id=str(payload["scope_id"]),
                sequence=payload["sequence"],
            )
        except InvalidCursorError:
            raise
        except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            raise InvalidCursorError from exc

    def _sign_payload(self, payload: bytes) -> str:
        """Sign serialized cursor payload bytes and return the public token."""

        encoded_payload = self._encode_bytes(payload)
        signature = hmac.new(self._key, encoded_payload.encode(), hashlib.sha256).digest()
        return f"{encoded_payload}.{self._encode_bytes(signature)}"

    def _verify_payload(self, cursor: str) -> dict[str, object]:
        """Verify a public cursor signature and return its decoded JSON object."""

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
        if not isinstance(payload, dict):
            raise InvalidCursorError
        return payload

    @staticmethod
    def _encode_bytes(value: bytes) -> str:
        """Encode bytes without URL-unsafe characters or padding."""

        return base64.urlsafe_b64encode(value).decode().rstrip("=")

    @staticmethod
    def _decode_bytes(value: str) -> bytes:
        """Decode URL-safe base64 after restoring required padding."""

        padding = "=" * (-len(value) % 4)
        return base64.urlsafe_b64decode(value + padding)
