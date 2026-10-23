"""Issue random user credentials and verify only their irreversible digests."""

import hashlib
import secrets
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from nexuspilot_api.core.errors import AuthenticationRequiredError, InvalidRequestError
from nexuspilot_api.features.identity.models.personal_api_key import UserApiKey
from nexuspilot_api.features.identity.schemas.authentication import PersonalKeyPrincipal
from nexuspilot_api.models import User
from nexuspilot_api.models.base import new_id, utc_now
from nexuspilot_api.schemas.base import ApiModel


class PersonalKeyCreate(BaseModel):
    """Accept fixed scopes and an optional timezone-qualified future expiry."""

    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=128)
    scopes: list[Literal["read", "execute"]] = Field(min_length=1, max_length=2)
    expires_at: datetime | None = None

    @field_validator("expires_at")
    @classmethod
    def validate_expiry(cls, value):
        """Reject ambiguous local times and already-expired credentials."""
        if value is not None and (value.tzinfo is None or value <= utc_now()):
            raise ValueError("Expiry must be a future time with timezone")
        return value


class PersonalKeyRead(ApiModel):
    """Expose revocation and usage metadata, never the verifier or secret."""

    api_key_id: str
    name: str
    masked_key: str
    scopes: list[str]
    created_at: datetime
    expires_at: datetime | None
    last_used_at: datetime | None
    revoked_at: datetime | None


class PersonalKeyCreated(PersonalKeyRead):
    """Return plaintext only for the one successful creation response."""

    key: str


async def create_personal_key(db, user_id, payload):
    """Commit a 256-bit random credential verifier, returning plaintext only to the caller."""
    identity = new_id()
    plaintext = f"np_{identity}.{secrets.token_urlsafe(32)}"
    row = UserApiKey(
        api_key_id=identity,
        user_id=user_id,
        name=payload.name,
        verifier=hashlib.sha256(plaintext.encode()).hexdigest(),
        masked_key=f"np_{identity}…{plaintext[-4:]}",
        scopes=sorted(set(payload.scopes)),
        expires_at=payload.expires_at.astimezone(UTC) if payload.expires_at else None,
    )
    db.add(row)
    await db.commit()
    return PersonalKeyCreated(**PersonalKeyRead.model_validate(row).model_dump(), key=plaintext)


async def resolve_personal_key(db, authorization):
    """Reject invalid, expired, revoked or disabled owners without any identity fallback."""
    if not authorization.startswith("Bearer np_") or len(authorization) > 256:
        raise AuthenticationRequiredError("Invalid personal API key")
    plaintext = authorization.removeprefix("Bearer ")
    identity, separator, secret = plaintext[3:].partition(".")
    if not separator or len(identity) != 36 or not secret:
        raise AuthenticationRequiredError("Invalid personal API key")
    row = await db.get(UserApiKey, identity, populate_existing=True)
    expected = row.verifier if row else "0" * 64
    valid = secrets.compare_digest(expected, hashlib.sha256(plaintext.encode()).hexdigest())
    owner = await db.get(User, row.user_id, populate_existing=True) if row else None
    now = utc_now()
    expiry = row.expires_at.replace(tzinfo=UTC) if row and row.expires_at else None
    if (
        not valid
        or row is None
        or row.revoked_at is not None
        or expiry
        and expiry <= now
        or owner is None
        or not owner.is_active
    ):
        raise AuthenticationRequiredError("Invalid personal API key")
    if not row.scopes or not set(row.scopes) <= {"read", "execute"}:
        raise InvalidRequestError("Invalid personal key permissions")
    row.last_used_at = now
    await db.commit()
    return PersonalKeyPrincipal(
        user_id=owner.user_id,
        display_name=owner.display_name,
        api_key_id=row.api_key_id,
        scopes=row.scopes,
    )
