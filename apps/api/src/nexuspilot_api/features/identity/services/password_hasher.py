"""Versioned Argon2id password hashing for local platform identities."""

import base64
import secrets

from cryptography.exceptions import InvalidKey
from cryptography.hazmat.primitives.kdf.argon2 import Argon2id

PASSWORD_HASH_VERSION = 1
PASSWORD_HASH_LENGTH_BYTES = 32
PASSWORD_SALT_LENGTH_BYTES = 16
PASSWORD_MEMORY_COST_KIB = 19_456
PASSWORD_ITERATIONS = 2
PASSWORD_PARALLELISM = 1
MINIMUM_PASSWORD_LENGTH = 12
MAXIMUM_PASSWORD_BYTES = 1_024


def _base64_encode(value: bytes) -> str:
    """Encode binary password material without ambiguous padding characters."""

    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _base64_decode(value: str) -> bytes:
    """Decode unpadded URL-safe base64 from the stored password format."""

    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _derive_password(password_bytes: bytes, salt: bytes) -> bytes:
    """Derive one fixed-size verifier with the current Argon2id policy."""

    return Argon2id(
        salt=salt,
        length=PASSWORD_HASH_LENGTH_BYTES,
        iterations=PASSWORD_ITERATIONS,
        lanes=PASSWORD_PARALLELISM,
        memory_cost=PASSWORD_MEMORY_COST_KIB,
    ).derive(password_bytes)


def hash_password(password: str) -> str:
    """Validate and hash a new password into the versioned database representation."""

    password_bytes = password.encode("utf-8")
    if len(password) < MINIMUM_PASSWORD_LENGTH or len(password_bytes) > MAXIMUM_PASSWORD_BYTES:
        raise ValueError(
            f"Password must contain at least {MINIMUM_PASSWORD_LENGTH} characters "
            f"and at most {MAXIMUM_PASSWORD_BYTES} UTF-8 bytes"
        )
    salt = secrets.token_bytes(PASSWORD_SALT_LENGTH_BYTES)
    password_hash = _derive_password(password_bytes, salt)
    return "$".join(
        (
            "argon2id",
            f"v={PASSWORD_HASH_VERSION}",
            (
                f"m={PASSWORD_MEMORY_COST_KIB},t={PASSWORD_ITERATIONS},"
                f"p={PASSWORD_PARALLELISM}"
            ),
            _base64_encode(salt),
            _base64_encode(password_hash),
        )
    )


def verify_password(password: str, encoded_password_hash: str) -> bool:
    """Verify a submitted password and safely reject corrupt or unsupported hashes."""

    try:
        algorithm, version, parameters, salt_value, hash_value = encoded_password_hash.split("$")
        if algorithm != "argon2id" or version != f"v={PASSWORD_HASH_VERSION}":
            return False
        expected_parameters = (
            f"m={PASSWORD_MEMORY_COST_KIB},t={PASSWORD_ITERATIONS},p={PASSWORD_PARALLELISM}"
        )
        if parameters != expected_parameters:
            return False
        password_bytes = password.encode("utf-8")
        if len(password_bytes) > MAXIMUM_PASSWORD_BYTES:
            return False
        Argon2id(
            salt=_base64_decode(salt_value),
            length=PASSWORD_HASH_LENGTH_BYTES,
            iterations=PASSWORD_ITERATIONS,
            lanes=PASSWORD_PARALLELISM,
            memory_cost=PASSWORD_MEMORY_COST_KIB,
        ).verify(password_bytes, _base64_decode(hash_value))
    except (InvalidKey, UnicodeError, ValueError):
        return False
    return True


# A real Argon2id verifier keeps unknown-email and wrong-password work comparable.
DUMMY_PASSWORD_HASH = hash_password("nexuspilot-dummy-password-never-used")
