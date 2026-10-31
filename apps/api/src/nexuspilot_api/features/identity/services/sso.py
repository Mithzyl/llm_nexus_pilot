"""Single-use authorization-code exchange; existing App owners are never reassigned."""

import base64
import re
import secrets
from datetime import UTC, timedelta
from urllib.parse import urlsplit

from sqlalchemy import select

from nexuspilot_api.core.errors import InvalidRequestError, ResourceConflictError
from nexuspilot_api.features.identity.models.applications import Application, ApplicationUserMapping
from nexuspilot_api.features.identity.models.authentication import UserLoginSession
from nexuspilot_api.features.identity.models.sso import ApplicationLoginGrant
from nexuspilot_api.features.identity.services.authentication_service import (
    authentication_token_hash,
)
from nexuspilot_api.models import User
from nexuspilot_api.models.base import utc_now
from nexuspilot_api.services.application_tools import application_tool_configurations


def pkce_challenge(verifier):
    """Apply RFC7636 S256 using the existing authentication SHA-256 helper, not a content hash."""
    if not re.fullmatch(r"[A-Za-z0-9._~-]{43,128}", verifier):
        raise InvalidRequestError("Invalid login exchange")
    return (
        base64.urlsafe_b64encode(bytes.fromhex(authentication_token_hash(verifier)))
        .rstrip(b"=")
        .decode()
    )


async def approved_client(db, app_id, redirect_uri=None):
    """Require an enabled configured App and an exact safe callback address."""
    config = application_tool_configurations().get(app_id)
    app = await db.get(Application, app_id, populate_existing=True)
    if config is None or app is None or not app.is_enabled or not config.login_redirect_uris:
        raise InvalidRequestError("Application login is unavailable")
    if redirect_uri is not None:
        parsed = urlsplit(redirect_uri)
        if (
            redirect_uri not in config.login_redirect_uris
            or parsed.fragment
            or parsed.query
            or parsed.username
            or parsed.password
            or not parsed.hostname
            or not (
                parsed.scheme == "https"
                or parsed.scheme == "http"
                and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
            )
        ):
            raise InvalidRequestError("Login callback is not approved")
    return config


async def live_user_session(db, owner, session_id):
    """Recheck the approving login on issue and exchange; logout or expiry invalidates the code."""
    session = await db.get(UserLoginSession, session_id, populate_existing=True)
    user = await db.get(User, owner, populate_existing=True)
    now = utc_now()
    if (
        session is None
        or session.user_id != owner
        or user is None
        or not user.is_active
        or session.revoked_at is not None
        or session.idle_expires_at.replace(tzinfo=UTC) <= now
        or session.absolute_expires_at.replace(tzinfo=UTC) <= now
    ):
        raise InvalidRequestError("Approving login is no longer active")
    return user


async def issue_code(db, *, owner, session_id, app_id, redirect_uri, challenge):
    """Issue five-minute consent for a browser-verified user, exact callback and S256 challenge."""
    await approved_client(db, app_id, redirect_uri)
    await live_user_session(db, owner, session_id)
    if not re.fullmatch(r"[A-Za-z0-9_-]{43}", challenge):
        raise InvalidRequestError("S256 challenge is required")
    code = secrets.token_urlsafe(32)
    db.add(
        ApplicationLoginGrant(
            application_id=app_id,
            user_id=owner,
            login_session_id=session_id,
            code_digest=authentication_token_hash(code),
            redirect_uri=redirect_uri,
            code_challenge=challenge,
            expires_at=utc_now() + timedelta(minutes=5),
        )
    )
    await db.commit()
    return code


async def exchange_code(db, *, app_id, code, redirect_uri, verifier):
    """Consume once after client authentication, without returning browser session credentials."""
    await approved_client(db, app_id, redirect_uri)
    row = await db.scalar(
        select(ApplicationLoginGrant)
        .where(
            ApplicationLoginGrant.code_digest == authentication_token_hash(code),
            ApplicationLoginGrant.application_id == app_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if (
        row is None
        or row.consumed_at is not None
        or row.expires_at.replace(tzinfo=UTC) <= utc_now()
        or row.redirect_uri != redirect_uri
        or not secrets.compare_digest(row.code_challenge, pkce_challenge(verifier))
    ):
        raise InvalidRequestError("Invalid login exchange")
    user = await live_user_session(db, row.user_id, row.login_session_id)
    mapping = await db.scalar(
        select(ApplicationUserMapping).where(
            ApplicationUserMapping.application_id == app_id,
            ApplicationUserMapping.pilot_principal_id == row.user_id,
        )
    )
    if mapping is None:
        from nexuspilot_api.features.identity.services.account_linking import linked_mapping

        mapping = await linked_mapping(db, owner=row.user_id, app_id=app_id)
    if mapping is not None and not mapping.is_enabled:
        raise InvalidRequestError("Application association is disabled")
    ticket = secrets.token_urlsafe(32) if mapping is None else None
    row.consumed_at = utc_now()
    row.registration_ticket_digest = authentication_token_hash(ticket) if ticket else None
    await db.commit()
    return {
        "subject": row.user_id,
        "display_name": user.display_name,
        "external_user_id": mapping.external_user_id if mapping else None,
        "registration_ticket": ticket,
    }


async def complete_registration(db, *, app_id, ticket, external_user_id):
    """Associate a new App user; existing other-owner accounts require explicit linking."""
    await approved_client(db, app_id)
    if not 1 <= len(external_user_id) <= 128 or external_user_id.strip() != external_user_id:
        raise InvalidRequestError("Invalid application subject")
    row = await db.scalar(
        select(ApplicationLoginGrant)
        .where(
            ApplicationLoginGrant.application_id == app_id,
            ApplicationLoginGrant.registration_ticket_digest == authentication_token_hash(ticket),
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is None or row.expires_at.replace(tzinfo=UTC) <= utc_now() or row.consumed_at is None:
        raise InvalidRequestError("Invalid registration completion")
    await live_user_session(db, row.user_id, row.login_session_id)
    # The owner lock serializes completion of separate login grants for the same user.
    await db.scalar(select(User).where(User.user_id == row.user_id).with_for_update())
    mapping = await db.scalar(
        select(ApplicationUserMapping)
        .where(
            ApplicationUserMapping.application_id == app_id,
            ApplicationUserMapping.pilot_principal_id == row.user_id,
        )
        .with_for_update()
    )
    existing = await db.scalar(
        select(ApplicationUserMapping)
        .where(
            ApplicationUserMapping.application_id == app_id,
            ApplicationUserMapping.external_user_id == external_user_id,
        )
        .with_for_update()
    )
    if (
        mapping
        and (mapping.external_user_id != external_user_id or not mapping.is_enabled)
        or existing
        and (existing.pilot_principal_id != row.user_id or not existing.is_enabled)
    ):
        raise ResourceConflictError("Existing account requires explicit linking")
    if mapping is None:
        if row.registration_completed:
            raise ResourceConflictError("Registration completion was already used")
        mapping = ApplicationUserMapping(
            application_id=app_id, external_user_id=external_user_id, pilot_principal_id=row.user_id
        )
        db.add(mapping)
    row.registration_completed = True
    await db.commit()
    return {"subject": row.user_id, "external_user_id": external_user_id}
