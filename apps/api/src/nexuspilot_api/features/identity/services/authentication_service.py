"""Local-password provisioning and opaque browser-session application service."""

import asyncio
import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from nexuspilot_api.core.config import Settings
from nexuspilot_api.core.errors import (
    AuthenticationCsrfError,
    AuthenticationIdentityConflictError,
    AuthenticationRateLimitError,
    AuthenticationRequiredError,
    InvalidAuthenticationCredentialsError,
    InvalidRequestError,
    ResourceConflictError,
    ResourceNotFoundError,
)
from nexuspilot_api.features.identity.models.authentication import (
    AuthenticationAuditEvent,
    UserAuthenticationIdentity,
    UserLoginSession,
    UserPasswordCredential,
)
from nexuspilot_api.features.identity.schemas.authentication import (
    AuthenticatedPrincipalRead,
    LocalWorkspacePrincipalRead,
    UserLoginSessionRead,
)
from nexuspilot_api.features.identity.services.password_hasher import (
    DUMMY_PASSWORD_HASH,
    hash_password,
    verify_password,
)
from nexuspilot_api.models import User
from nexuspilot_api.models.base import utc_now
from nexuspilot_api.schemas.users import UserCreate
from nexuspilot_api.services.user_service import create_user

BROWSER_SCOPES = ("model:invoke", "sessions:read", "sessions:write")
SESSION_TOKEN_PREFIX = "nps_"


async def resolve_local_workspace_principal(
    db_session: AsyncSession,
) -> LocalWorkspacePrincipalRead:
    """Create/reuse only the historical local owner; reject disabled or login-bound owners.

    Caller must first enforce local mode and the deployment service key. Concurrent
    first reads reuse the uniquely committed User, without creating a password or session.
    """

    local_user_id = "nexuspilot-web"
    local_user = await db_session.get(User, local_user_id)
    if local_user is None:
        try:
            local_user = await create_user(
                db_session, UserCreate(user_id=local_user_id, display_name="本地工作区"),
            )
        except ResourceConflictError:
            # The unique owner may have been created by another browser's initial read.
            await db_session.rollback()
            local_user = await db_session.get(User, local_user_id)
    identity = await db_session.scalar(select(UserAuthenticationIdentity).where(
        UserAuthenticationIdentity.user_id == local_user_id,
    ))
    if local_user is None or identity is not None:
        raise ResourceConflictError("本地资源所有者不可用，请检查账号配置。")
    if not local_user.is_active:
        raise AuthenticationRequiredError("本地资源所有者已停用。")
    return LocalWorkspacePrincipalRead(
        user_id=local_user.user_id, display_name="本地工作区", scopes=list(BROWSER_SCOPES),
    )


@dataclass(frozen=True)
class BrowserLoginResult:
    """Carry the committed public principal and one-time browser cookie values."""

    principal: AuthenticatedPrincipalRead
    session_token: str
    csrf_token: str


def normalize_login_email(email: str) -> str:
    """Normalize a bounded email identity and reject structurally invalid input."""

    normalized_email = email.strip().casefold()
    if (
        len(normalized_email) > 320
        or normalized_email.count("@") != 1
        or any(character.isspace() or ord(character) < 32 for character in normalized_email)
    ):
        raise InvalidRequestError("邮箱格式无效。")
    local_part, domain = normalized_email.split("@", 1)
    if not local_part or not domain or "." not in domain:
        raise InvalidRequestError("邮箱格式无效。")
    return normalized_email


def authentication_fingerprint(value: str, settings: Settings) -> str:
    """Create a non-reversible audit/rate-limit key using a server-side secret."""

    return hmac.new(
        settings.internal_api_key.get_secret_value().encode("utf-8"),
        value.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def authentication_token_hash(token: str) -> str:
    """Hash a high-entropy opaque token before database lookup or persistence."""

    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def validate_authentication_origin(origin: str | None, settings: Settings) -> None:
    """Require an exact configured Origin for every browser authentication mutation."""

    normalized_origin = origin.rstrip("/") if origin else None
    if normalized_origin not in settings.allowed_authentication_origins():
        raise AuthenticationCsrfError("请求来源未获授权。")


def validate_login_session_csrf(
    *,
    login_session: UserLoginSession,
    csrf_cookie: str | None,
    csrf_header: str | None,
) -> None:
    """Verify matching browser/header CSRF secrets against the session-bound digest."""

    if (
        not csrf_cookie
        or not csrf_header
        or not secrets.compare_digest(csrf_cookie, csrf_header)
        or not secrets.compare_digest(
            authentication_token_hash(csrf_header),
            login_session.csrf_token_hash,
        )
    ):
        raise AuthenticationCsrfError("跨站请求伪造校验失败。")


def _as_utc(value: datetime) -> datetime:
    """Normalize database datetimes because SQLite drops timezone metadata in tests."""

    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


async def provision_local_password_user(
    db_session: AsyncSession,
    *,
    user_id: str,
    display_name: str,
    email: str,
    password: str,
) -> UserAuthenticationIdentity:
    """Atomically provision a User owner, verified email identity, and Argon2id password."""

    normalized_email = normalize_login_email(email)
    owner = UserCreate(user_id=user_id, display_name=display_name)
    encoded_password_hash = await asyncio.to_thread(hash_password, password)
    existing_user = await db_session.get(User, user_id)
    if existing_user is not None:
        raise ResourceConflictError("User already exists")
    existing_identity = await db_session.scalar(
        select(UserAuthenticationIdentity).where(
            UserAuthenticationIdentity.identity_provider == "local_password",
            UserAuthenticationIdentity.normalized_email == normalized_email,
        )
    )
    if existing_identity is not None:
        raise ResourceConflictError("Authentication identity already exists")

    user = User(user_id=owner.user_id, display_name=owner.display_name, is_active=True)
    identity = UserAuthenticationIdentity(
        user_id=user_id,
        identity_provider="local_password",
        email=email.strip(),
        normalized_email=normalized_email,
        is_verified=True,
    )
    try:
        db_session.add(user)
        await db_session.flush()
        db_session.add(identity)
        await db_session.flush()
        db_session.add(
            UserPasswordCredential(
                auth_identity_id=identity.auth_identity_id,
                password_hash=encoded_password_hash,
                password_hash_algorithm="argon2id",
                password_hash_version=1,
            )
        )
        await db_session.commit()
    except IntegrityError as error:
        await db_session.rollback()
        raise ResourceConflictError("User or authentication identity already exists") from error
    await db_session.refresh(identity)
    return identity


async def _record_authentication_event(
    db_session: AsyncSession,
    *,
    event_type: str,
    outcome: str,
    identifier_fingerprint: str | None,
    client_fingerprint: str | None,
    user_id: str | None = None,
    auth_identity_id: str | None = None,
    login_session_id: str | None = None,
    metadata_json: dict[str, object] | None = None,
) -> None:
    """Stage one redacted authentication audit fact in the caller's transaction."""

    db_session.add(
        AuthenticationAuditEvent(
            user_id=user_id,
            auth_identity_id=auth_identity_id,
            login_session_id=login_session_id,
            event_type=event_type,
            outcome=outcome,
            identifier_fingerprint=identifier_fingerprint,
            client_fingerprint=client_fingerprint,
            metadata_json=metadata_json or {},
        )
    )


async def _enforce_login_failure_limit(
    db_session: AsyncSession,
    *,
    identifier_fingerprint: str,
    client_fingerprint: str,
    settings: Settings,
) -> None:
    """Reject repeated identity/client failures using committed database audit facts."""

    window_started_at = utc_now() - timedelta(
        seconds=settings.authentication_login_failure_window_seconds
    )
    failure_count = await db_session.scalar(
        select(func.count(AuthenticationAuditEvent.authentication_audit_event_id)).where(
            AuthenticationAuditEvent.event_type == "login_failed",
            AuthenticationAuditEvent.identifier_fingerprint == identifier_fingerprint,
            AuthenticationAuditEvent.client_fingerprint == client_fingerprint,
            AuthenticationAuditEvent.occurred_at >= window_started_at,
        )
    )
    if int(failure_count or 0) < settings.authentication_login_failure_limit:
        return
    await _record_authentication_event(
        db_session,
        event_type="login_rate_limited",
        outcome="rejected",
        identifier_fingerprint=identifier_fingerprint,
        client_fingerprint=client_fingerprint,
    )
    await db_session.commit()
    raise AuthenticationRateLimitError("登录尝试过于频繁，请稍后重试。")


async def authenticate_local_password(
    db_session: AsyncSession,
    *,
    email: str,
    password: str,
    device_name: str | None,
    client_identifier: str,
    settings: Settings,
) -> BrowserLoginResult:
    """Verify local credentials, persist one opaque session, and return cookie secrets once."""

    try:
        normalized_email = normalize_login_email(email)
    except InvalidRequestError:
        normalized_email = email.strip().casefold()[:320]
    identifier_fingerprint = authentication_fingerprint(normalized_email, settings)
    client_fingerprint = authentication_fingerprint(client_identifier, settings)
    await _enforce_login_failure_limit(
        db_session,
        identifier_fingerprint=identifier_fingerprint,
        client_fingerprint=client_fingerprint,
        settings=settings,
    )
    row = (
        await db_session.execute(
            select(UserAuthenticationIdentity, UserPasswordCredential, User)
            .join(
                UserPasswordCredential,
                UserPasswordCredential.auth_identity_id
                == UserAuthenticationIdentity.auth_identity_id,
            )
            .join(User, User.user_id == UserAuthenticationIdentity.user_id)
            .where(
                UserAuthenticationIdentity.identity_provider == "local_password",
                UserAuthenticationIdentity.normalized_email == normalized_email,
            )
        )
    ).one_or_none()
    encoded_password_hash = row[1].password_hash if row is not None else DUMMY_PASSWORD_HASH
    password_matches = await asyncio.to_thread(
        verify_password,
        password,
        encoded_password_hash,
    )
    if row is None or not password_matches or not row[2].is_active or not row[0].is_verified:
        await _record_authentication_event(
            db_session,
            event_type="login_failed",
            outcome="rejected",
            identifier_fingerprint=identifier_fingerprint,
            client_fingerprint=client_fingerprint,
            user_id=row[2].user_id if row is not None else None,
            auth_identity_id=row[0].auth_identity_id if row is not None else None,
        )
        await db_session.commit()
        raise InvalidAuthenticationCredentialsError("邮箱或密码不正确。")

    identity, _password_credential, user = row
    now = utc_now()
    session_token = f"{SESSION_TOKEN_PREFIX}{secrets.token_urlsafe(32)}"
    csrf_token = secrets.token_urlsafe(32)
    login_session = UserLoginSession(
        user_id=user.user_id,
        auth_identity_id=identity.auth_identity_id,
        session_token_hash=authentication_token_hash(session_token),
        csrf_token_hash=authentication_token_hash(csrf_token),
        device_name=device_name,
        authenticated_at=now,
        last_used_at=now,
        idle_expires_at=now + timedelta(seconds=settings.authentication_session_idle_seconds),
        absolute_expires_at=now
        + timedelta(seconds=settings.authentication_session_absolute_seconds),
    )
    db_session.add(login_session)
    await db_session.flush()
    await _record_authentication_event(
        db_session,
        event_type="login_succeeded",
        outcome="succeeded",
        identifier_fingerprint=identifier_fingerprint,
        client_fingerprint=client_fingerprint,
        user_id=user.user_id,
        auth_identity_id=identity.auth_identity_id,
        login_session_id=login_session.login_session_id,
    )
    await db_session.commit()
    return BrowserLoginResult(
        principal=AuthenticatedPrincipalRead(
            user_id=user.user_id,
            display_name=user.display_name,
            login_session_id=login_session.login_session_id,
            scopes=list(BROWSER_SCOPES),
        ),
        session_token=session_token,
        csrf_token=csrf_token,
    )


async def resolve_browser_principal(
    db_session: AsyncSession,
    *,
    session_token: str | None,
    authorization_header: str | None,
    settings: Settings,
) -> AuthenticatedPrincipalRead:
    """Resolve one active Cookie session and reject missing or ambiguous identities."""

    if session_token and authorization_header:
        raise AuthenticationIdentityConflictError(
            "不能同时提交浏览器会话和 Bearer 身份。"
        )
    if authorization_header:
        raise AuthenticationRequiredError("个人 API Key 认证尚未启用。")
    if not session_token:
        raise AuthenticationRequiredError("需要登录。")
    row = (
        await db_session.execute(
            select(UserLoginSession, User)
            .join(User, User.user_id == UserLoginSession.user_id)
            .where(
                UserLoginSession.session_token_hash
                == authentication_token_hash(session_token)
            )
        )
    ).one_or_none()
    now = utc_now()
    if row is None:
        raise AuthenticationRequiredError("登录会话无效或已过期。")
    login_session, user = row
    if (
        login_session.revoked_at is not None
        or _as_utc(login_session.idle_expires_at) <= now
        or _as_utc(login_session.absolute_expires_at) <= now
        or not user.is_active
    ):
        raise AuthenticationRequiredError("登录会话无效或已过期。")

    if _as_utc(login_session.last_used_at) <= now - timedelta(minutes=5):
        login_session.last_used_at = now
        login_session.idle_expires_at = min(
            now + timedelta(seconds=settings.authentication_session_idle_seconds),
            _as_utc(login_session.absolute_expires_at),
        )
        await db_session.commit()
    return AuthenticatedPrincipalRead(
        user_id=user.user_id,
        display_name=user.display_name,
        login_session_id=login_session.login_session_id,
        scopes=list(BROWSER_SCOPES),
    )


async def get_login_session_by_token(
    db_session: AsyncSession,
    session_token: str,
) -> UserLoginSession | None:
    """Load a browser session including terminal rows for idempotent logout handling."""

    return await db_session.scalar(
        select(UserLoginSession).where(
            UserLoginSession.session_token_hash == authentication_token_hash(session_token)
        )
    )


async def revoke_login_session(
    db_session: AsyncSession,
    *,
    login_session: UserLoginSession,
    reason: str,
) -> None:
    """Idempotently revoke one browser session and record the security event."""

    if login_session.revoked_at is None:
        login_session.revoked_at = utc_now()
        login_session.revoked_reason = reason
        await _record_authentication_event(
            db_session,
            event_type="login_session_revoked",
            outcome="succeeded",
            identifier_fingerprint=None,
            client_fingerprint=None,
            user_id=login_session.user_id,
            auth_identity_id=login_session.auth_identity_id,
            login_session_id=login_session.login_session_id,
            metadata_json={"reason": reason},
        )
        await db_session.commit()


async def revoke_all_user_login_sessions(
    db_session: AsyncSession,
    *,
    user_id: str,
    actor_login_session_id: str,
) -> None:
    """Revoke every active browser session owned by one authenticated user."""

    now = utc_now()
    await db_session.execute(
        update(UserLoginSession)
        .where(UserLoginSession.user_id == user_id, UserLoginSession.revoked_at.is_(None))
        .values(revoked_at=now, revoked_reason="security_revoke_all")
    )
    await _record_authentication_event(
        db_session,
        event_type="all_login_sessions_revoked",
        outcome="succeeded",
        identifier_fingerprint=None,
        client_fingerprint=None,
        user_id=user_id,
        login_session_id=actor_login_session_id,
    )
    await db_session.commit()


async def change_local_password(
    db_session: AsyncSession,
    *,
    current_login_session: UserLoginSession,
    current_password: str,
    new_password: str,
) -> None:
    """Replace the current local verifier and revoke every other browser session."""

    password_credential = await db_session.get(
        UserPasswordCredential,
        current_login_session.auth_identity_id,
    )
    if password_credential is None or not await asyncio.to_thread(
        verify_password,
        current_password,
        password_credential.password_hash if password_credential else DUMMY_PASSWORD_HASH,
    ):
        raise InvalidAuthenticationCredentialsError("当前密码不正确。")
    try:
        replacement_hash = await asyncio.to_thread(hash_password, new_password)
    except ValueError as error:
        raise InvalidRequestError("新密码不符合长度要求。") from error

    now = utc_now()
    password_credential.password_hash = replacement_hash
    password_credential.password_hash_algorithm = "argon2id"
    password_credential.password_hash_version = 1
    password_credential.password_changed_at = now
    await db_session.execute(
        update(UserLoginSession)
        .where(
            UserLoginSession.user_id == current_login_session.user_id,
            UserLoginSession.login_session_id != current_login_session.login_session_id,
            UserLoginSession.revoked_at.is_(None),
        )
        .values(revoked_at=now, revoked_reason="password_changed")
    )
    await _record_authentication_event(
        db_session,
        event_type="password_changed",
        outcome="succeeded",
        identifier_fingerprint=None,
        client_fingerprint=None,
        user_id=current_login_session.user_id,
        auth_identity_id=current_login_session.auth_identity_id,
        login_session_id=current_login_session.login_session_id,
    )
    await db_session.commit()


async def list_user_login_sessions(
    db_session: AsyncSession,
    *,
    user_id: str,
    current_login_session_id: str,
) -> list[UserLoginSessionRead]:
    """List one user's browser sessions without returning authentication secrets."""

    sessions = list(
        (
            await db_session.scalars(
                select(UserLoginSession)
                .where(UserLoginSession.user_id == user_id)
                .order_by(UserLoginSession.authenticated_at.desc())
                .limit(100)
            )
        ).all()
    )
    return [
        UserLoginSessionRead.model_validate(login_session).model_copy(
            update={"is_current": login_session.login_session_id == current_login_session_id}
        )
        for login_session in sessions
    ]


async def require_owned_login_session(
    db_session: AsyncSession,
    *,
    user_id: str,
    login_session_id: str,
) -> UserLoginSession:
    """Load one owned browser session while hiding cross-user identifiers as not found."""

    login_session = await db_session.get(UserLoginSession, login_session_id)
    if login_session is None or login_session.user_id != user_id:
        raise ResourceNotFoundError("Login session")
    return login_session
