"""Email proof resets an existing local password; it never registers or reassigns an account."""

import asyncio
import json
import secrets
from datetime import timedelta

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from nexuspilot_api.core.errors import AuthenticationRateLimitError, InvalidRequestError
from nexuspilot_api.features.identity.models.authentication import (
    AuthenticationAuditEvent,
    UserAuthenticationIdentity,
    UserLoginSession,
    UserPasswordCredential,
)
from nexuspilot_api.features.identity.models.password_reset import PasswordResetChallenge
from nexuspilot_api.features.identity.services.authentication_service import (
    _as_utc,
    authentication_fingerprint,
    normalize_login_email,
)
from nexuspilot_api.features.identity.services.password_hasher import hash_password
from nexuspilot_api.features.identity.services.registration import send_verification_email
from nexuspilot_api.models import User
from nexuspilot_api.models.base import utc_now


def reset_cipher(settings):
    """Require explicit reset enablement and the existing private email-proof encryption key."""
    if not settings.password_reset_enabled or not settings.authentication_enabled:
        raise InvalidRequestError("密码找回尚未开放。")
    try:
        return Fernet(settings.registration_verification_key.get_secret_value().encode())
    except (AttributeError, ValueError):
        raise InvalidRequestError("邮箱验证服务尚未配置。") from None


def credential_query(email):
    """Select the exact local identity and credential; callers choose whether to lock it."""
    return (
        select(UserAuthenticationIdentity, UserPasswordCredential, User)
        .join(
            UserPasswordCredential,
            UserPasswordCredential.auth_identity_id == UserAuthenticationIdentity.auth_identity_id,
        )
        .join(User, User.user_id == UserAuthenticationIdentity.user_id)
        .where(
            UserAuthenticationIdentity.identity_provider == "local_password",
            UserAuthenticationIdentity.normalized_email == email,
        )
    )


async def request_reset(db, email, settings, *, client_identifier):
    """Reserve a rate-limited code; unknown or inactive accounts get the same public response."""
    cipher = reset_cipher(settings)
    email = normalize_login_email(email)
    now = utc_now()
    client = authentication_fingerprint(client_identifier, settings)
    count = await db.scalar(
        select(func.count())
        .select_from(AuthenticationAuditEvent)
        .where(
            AuthenticationAuditEvent.event_type == "password_reset_requested",
            AuthenticationAuditEvent.client_fingerprint == client,
            AuthenticationAuditEvent.occurred_at >= now - timedelta(minutes=15),
        )
    )
    if count >= 5:
        raise AuthenticationRateLimitError("验证邮件申请过于频繁，请稍后重试。")
    row = await db.scalar(
        select(PasswordResetChallenge)
        .where(PasswordResetChallenge.normalized_email == email)
        .with_for_update()
    )
    if row and _as_utc(row.sent_at) > now - timedelta(seconds=60):
        raise AuthenticationRateLimitError("请稍后再申请验证码。")
    credential = (await db.execute(credential_query(email))).one_or_none()
    available = credential is not None and credential[0].is_verified and credential[2].is_active
    if row is None:
        row = PasswordResetChallenge(normalized_email=email)
        db.add(row)
    code = secrets.token_urlsafe(24)
    # Bind to the existing credential verifier, not a low-precision database timestamp alone.
    proof = {"code": code, "credential": credential[1].password_hash if available else None}
    encrypted = cipher.encrypt(json.dumps(proof).encode()).decode()
    row.encrypted_code = encrypted
    row.auth_identity_id = credential[0].auth_identity_id if available else None
    row.credential_updated_at = credential[1].password_changed_at if available else None
    row.sent_at, row.expires_at = now, now + timedelta(minutes=15)
    row.failed_attempts, row.delivered = 0, False
    db.add(
        AuthenticationAuditEvent(
            event_type="password_reset_requested",
            outcome="requested",
            client_fingerprint=client,
            metadata_json={},
        )
    )
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise AuthenticationRateLimitError("请稍后再申请验证码。") from None
    if not available:
        return
    try:
        await send_verification_email(email, code, settings, purpose="password_reset")
    except InvalidRequestError:
        # Do not reveal account existence via a delivery-only failure response.
        db.add(
            AuthenticationAuditEvent(
                event_type="password_reset_delivery",
                outcome="failed",
                client_fingerprint=client,
                metadata_json={},
            )
        )
        await db.commit()
        return
    await db.execute(
        update(PasswordResetChallenge)
        .where(
            PasswordResetChallenge.normalized_email == email,
            PasswordResetChallenge.encrypted_code == encrypted,
        )
        .values(delivered=True)
    )
    await db.commit()


async def verify_reset(db, email, code, password, settings):
    """Consume proof, change the verifier and revoke all browser sessions atomically."""
    cipher = reset_cipher(settings)
    email = normalize_login_email(email)
    if not 12 <= len(password) <= 1024:
        raise InvalidRequestError("新密码不符合长度要求。")
    row = await db.scalar(
        select(PasswordResetChallenge)
        .where(PasswordResetChallenge.normalized_email == email)
        .with_for_update()
    )
    valid = bool(
        row
        and row.delivered
        and row.encrypted_code
        and row.failed_attempts < 5
        and _as_utc(row.expires_at) > utc_now()
    )
    proof = {}
    try:
        if valid:
            proof = json.loads(cipher.decrypt(row.encrypted_code.encode()))
            valid = isinstance(proof, dict) and isinstance(proof.get("code"), str)
            valid = valid and secrets.compare_digest(proof["code"], code)
    except (InvalidToken, ValueError, TypeError):
        valid = False
    credential = (
        await db.execute(
            credential_query(email).with_for_update().execution_options(populate_existing=True)
        )
    ).one_or_none()
    valid = bool(
        valid
        and credential
        and credential[0].auth_identity_id == row.auth_identity_id
        and credential[0].is_verified
        and credential[2].is_active
        and isinstance(proof.get("credential"), str)
        and secrets.compare_digest(credential[1].password_hash, proof["credential"])
        and row.credential_updated_at is not None
        and _as_utc(credential[1].password_changed_at) == _as_utc(row.credential_updated_at)
    )
    if not valid:
        if row:
            row.failed_attempts += 1
            await db.commit()
        raise InvalidRequestError("验证码无效或已过期，请重新申请。")
    now = utc_now()
    credential[1].password_hash = await asyncio.to_thread(hash_password, password)
    credential[1].password_hash_algorithm = "argon2id"
    credential[1].password_hash_version = 1
    credential[1].password_changed_at = now
    await db.execute(
        update(UserLoginSession)
        .where(
            UserLoginSession.user_id == credential[2].user_id, UserLoginSession.revoked_at.is_(None)
        )
        .values(revoked_at=now, revoked_reason="password_reset")
    )
    row.encrypted_code, row.delivered = None, False
    db.add(
        AuthenticationAuditEvent(
            event_type="password_reset_completed",
            outcome="succeeded",
            user_id=credential[2].user_id,
            auth_identity_id=credential[0].auth_identity_id,
            metadata_json={},
        )
    )
    await db.commit()
