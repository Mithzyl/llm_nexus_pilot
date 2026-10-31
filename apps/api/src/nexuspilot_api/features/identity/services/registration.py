"""Default-disabled SMTP verification; create login credentials only after email proof."""

import asyncio
import secrets
import smtplib
import ssl
from datetime import timedelta
from email.message import EmailMessage

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from nexuspilot_api.core.errors import AuthenticationRateLimitError, InvalidRequestError
from nexuspilot_api.features.identity.models.authentication import (
    AuthenticationAuditEvent,
    UserAuthenticationIdentity,
    UserPasswordCredential,
)
from nexuspilot_api.features.identity.models.registration import RegistrationChallenge
from nexuspilot_api.features.identity.services.authentication_service import (
    _as_utc,
    authentication_fingerprint,
    normalize_login_email,
)
from nexuspilot_api.features.identity.services.password_hasher import hash_password
from nexuspilot_api.models import User
from nexuspilot_api.models.base import new_id, utc_now


def verification_cipher(settings):
    """Require an explicit private encryption key; registration never inherits a public default."""
    if not settings.registration_enabled:
        raise InvalidRequestError("注册尚未开放。")
    try:
        return Fernet(settings.registration_verification_key.get_secret_value().encode())
    except (AttributeError, ValueError):
        raise InvalidRequestError("注册验证服务尚未配置。") from None


async def send_verification_email(email, code, settings, *, purpose="registration"):
    """Send one bounded SMTP message; do not log credentials, code, or server exception bodies."""
    host = settings.registration_smtp_host
    if not host or not settings.registration_smtp_sender:
        raise InvalidRequestError("验证邮件服务尚未配置。")
    if not settings.registration_smtp_starttls and host not in {"localhost", "127.0.0.1", "::1"}:
        raise InvalidRequestError("非本地邮件服务必须启用TLS。")
    message = EmailMessage()
    message["From"] = settings.registration_smtp_sender
    message["To"] = email
    label = "密码重置" if purpose == "password_reset" else "注册验证"
    message["Subject"] = f"NexusPilot {label}"
    message.set_content(f"你的{label}码：\n{code}\n\n15分钟内有效。若非本人申请，请忽略本邮件。")

    def deliver():
        """Use the configured relay once; SMTP uncertainty must not trigger automatic resends."""
        with smtplib.SMTP(host, settings.registration_smtp_port, timeout=10) as smtp:
            if settings.registration_smtp_starttls:
                smtp.starttls(context=ssl.create_default_context())
            if settings.registration_smtp_username:
                smtp.login(
                    settings.registration_smtp_username,
                    settings.registration_smtp_password.get_secret_value()
                    if settings.registration_smtp_password
                    else "",
                )
            refused = smtp.send_message(message)
            if refused:
                raise smtplib.SMTPException("Recipient rejected")

    try:
        await asyncio.to_thread(deliver)
    except (OSError, smtplib.SMTPException, ValueError):
        raise InvalidRequestError("验证邮件暂时无法发送，请稍后重试。") from None


async def request_registration(db, email, settings, *, client_identifier="local"):
    """Reserve one rate-limited challenge, deliver it, and only then allow verification."""
    cipher = verification_cipher(settings)
    email = normalize_login_email(email)
    now = utc_now()
    # Reuse existing authentication audit identifiers; no new content-hash mechanism.
    client = authentication_fingerprint(client_identifier, settings)
    sent_count = await db.scalar(
        select(func.count())
        .select_from(AuthenticationAuditEvent)
        .where(
            AuthenticationAuditEvent.event_type == "registration_requested",
            AuthenticationAuditEvent.client_fingerprint == client,
            AuthenticationAuditEvent.occurred_at >= now - timedelta(minutes=15),
        )
    )
    if sent_count >= 5:
        raise AuthenticationRateLimitError("验证邮件申请过于频繁，请稍后重试。")
    row = await db.scalar(
        select(RegistrationChallenge)
        .where(RegistrationChallenge.normalized_email == email)
        .with_for_update()
    )
    if row and _as_utc(row.sent_at) > now - timedelta(seconds=60):
        raise AuthenticationRateLimitError("请稍后再申请验证码。")
    if row is None:
        row = RegistrationChallenge(normalized_email=email)
        db.add(row)
    code = secrets.token_urlsafe(24)
    encrypted = cipher.encrypt(code.encode()).decode()
    row.encrypted_code = encrypted
    row.expires_at = now + timedelta(minutes=15)
    row.sent_at = now
    row.failed_attempts = 0
    row.delivered = False
    db.add(
        AuthenticationAuditEvent(
            event_type="registration_requested",
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
    existing = await db.scalar(
        select(UserAuthenticationIdentity).where(
            UserAuthenticationIdentity.identity_provider == "local_password",
            UserAuthenticationIdentity.normalized_email == email,
        )
    )
    if existing is not None:
        # Do not send reset-like mail or allow a registration to change existing credentials.
        return
    await send_verification_email(email, code, settings)
    await db.execute(
        update(RegistrationChallenge)
        .where(
            RegistrationChallenge.normalized_email == email,
            RegistrationChallenge.encrypted_code == encrypted,
        )
        .values(delivered=True)
    )
    await db.commit()


async def verify_registration(db, email, code, display_name, password, settings):
    """Consume email proof and create owner/identity/password in one transaction, without login."""
    cipher = verification_cipher(settings)
    email = normalize_login_email(email)
    if not display_name.strip() or len(display_name) > 128 or not 12 <= len(password) <= 1024:
        raise InvalidRequestError("请检查昵称和密码长度。")
    row = await db.scalar(
        select(RegistrationChallenge)
        .where(RegistrationChallenge.normalized_email == email)
        .with_for_update()
    )
    valid = bool(
        row
        and row.delivered
        and row.encrypted_code
        and row.failed_attempts < 5
        and _as_utc(row.expires_at) > utc_now()
    )
    try:
        valid = valid and secrets.compare_digest(
            cipher.decrypt(row.encrypted_code.encode()), code.encode()
        )
    except InvalidToken:
        valid = False
    if not valid:
        if row:
            row.failed_attempts += 1
            await db.commit()
        raise InvalidRequestError("验证码无效或已过期，请重新申请。")
    user_id = new_id()
    user = User(user_id=user_id, display_name=display_name.strip(), is_active=True)
    identity = UserAuthenticationIdentity(
        user_id=user_id,
        identity_provider="local_password",
        email=email,
        normalized_email=email,
        is_verified=True,
    )
    try:
        db.add(user)
        await db.flush()
        db.add(identity)
        await db.flush()
        db.add(
            UserPasswordCredential(
                auth_identity_id=identity.auth_identity_id,
                password_hash=await asyncio.to_thread(hash_password, password),
            )
        )
        row.encrypted_code = None
        row.delivered = False
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise InvalidRequestError("无法完成注册，请登录或重新申请验证码。") from None
    return user_id
