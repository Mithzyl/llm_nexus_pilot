"""Public login and authenticated browser-session management controllers."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from nexuspilot_api.core.config import Settings, get_settings
from nexuspilot_api.features.identity.dependencies import (
    CSRF_COOKIE_NAME,
    SESSION_COOKIE_NAME,
    AuthenticatedPrincipalDependency,
    get_workspace_principal,
    require_browser_authentication_enabled,
)
from nexuspilot_api.features.identity.models.authentication import UserLoginSession
from nexuspilot_api.features.identity.schemas.authentication import (
    AuthenticatedPrincipalRead,
    BrowserLoginRequest,
    PasswordChangeRequest,
    UserLoginSessionRead,
    WorkspacePrincipalRead,
)
from nexuspilot_api.features.identity.services.authentication_service import (
    authenticate_local_password,
    change_local_password,
    get_login_session_by_token,
    list_user_login_sessions,
    require_owned_login_session,
    revoke_all_user_login_sessions,
    revoke_login_session,
    validate_authentication_origin,
    validate_login_session_csrf,
)
from nexuspilot_api.infrastructure.database import get_database_session

router = APIRouter(prefix="/api/v1/auth", tags=["authentication"])
DatabaseSessionDependency = Annotated[AsyncSession, Depends(get_database_session)]
SettingsDependency = Annotated[Settings, Depends(get_settings)]


def _set_no_store(response: Response) -> None:
    """Prevent authentication responses and Cookie-bearing bodies from being cached."""

    response.headers["Cache-Control"] = "no-store"


def _set_authentication_cookies(
    response: Response,
    *,
    session_token: str,
    csrf_token: str,
    settings: Settings,
) -> None:
    """Set the opaque HttpOnly session and readable double-submit CSRF cookies."""

    max_age = settings.authentication_session_absolute_seconds
    response.set_cookie(
        SESSION_COOKIE_NAME,
        session_token,
        max_age=max_age,
        httponly=True,
        secure=settings.authentication_cookie_secure,
        samesite="lax",
        path="/",
    )
    response.set_cookie(
        CSRF_COOKIE_NAME,
        csrf_token,
        max_age=max_age,
        httponly=False,
        secure=settings.authentication_cookie_secure,
        samesite="lax",
        path="/",
    )


def _clear_authentication_cookies(response: Response, settings: Settings) -> None:
    """Expire both browser authentication cookies using their original attributes."""

    response.delete_cookie(
        SESSION_COOKIE_NAME,
        httponly=True,
        secure=settings.authentication_cookie_secure,
        samesite="lax",
        path="/",
    )
    response.delete_cookie(
        CSRF_COOKIE_NAME,
        httponly=False,
        secure=settings.authentication_cookie_secure,
        samesite="lax",
        path="/",
    )


def _validate_request_csrf(
    request: Request,
    *,
    login_session: UserLoginSession,
    settings: Settings,
) -> None:
    """Validate exact Origin and session-bound CSRF proof for one Cookie mutation."""

    validate_authentication_origin(request.headers.get("origin"), settings)
    validate_login_session_csrf(
        login_session=login_session,
        csrf_cookie=request.cookies.get(CSRF_COOKIE_NAME),
        csrf_header=request.headers.get("x-csrf-token"),
    )


@router.post(
    "/login",
    response_model=AuthenticatedPrincipalRead,
    dependencies=[Depends(require_browser_authentication_enabled)],
)
async def post_browser_login(
    payload: BrowserLoginRequest,
    request: Request,
    response: Response,
    db_session: DatabaseSessionDependency,
    settings: SettingsDependency,
) -> AuthenticatedPrincipalRead:
    """Authenticate a provisioned local user and establish a new opaque browser session."""

    validate_authentication_origin(request.headers.get("origin"), settings)
    client_identifier = request.client.host if request.client else "unknown-client"
    login_result = await authenticate_local_password(
        db_session,
        email=payload.email,
        password=payload.password,
        device_name=payload.device_name,
        client_identifier=client_identifier,
        settings=settings,
    )
    _set_no_store(response)
    _set_authentication_cookies(
        response,
        session_token=login_result.session_token,
        csrf_token=login_result.csrf_token,
        settings=settings,
    )
    return login_result.principal


@router.get("/me", response_model=WorkspacePrincipalRead)
async def get_current_workspace_principal(
    principal: Annotated[WorkspacePrincipalRead, Depends(get_workspace_principal)],
    response: Response,
) -> WorkspacePrincipalRead:
    """Return configured local ownership or the logged-in principal, never secret metadata."""

    _set_no_store(response)
    return principal


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_browser_authentication_enabled)],
)
async def post_browser_logout(
    request: Request,
    db_session: DatabaseSessionDependency,
    settings: SettingsDependency,
) -> Response:
    """Idempotently revoke the current Cookie session and expire browser cookies."""

    validate_authentication_origin(request.headers.get("origin"), settings)
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    _set_no_store(response)
    session_token = request.cookies.get(SESSION_COOKIE_NAME)
    if not session_token:
        _clear_authentication_cookies(response, settings)
        return response
    login_session = await get_login_session_by_token(db_session, session_token)
    if login_session is None:
        _clear_authentication_cookies(response, settings)
        return response
    _validate_request_csrf(
        request,
        login_session=login_session,
        settings=settings,
    )
    await revoke_login_session(
        db_session,
        login_session=login_session,
        reason="user_logout",
    )
    _clear_authentication_cookies(response, settings)
    return response


@router.get("/sessions", response_model=list[UserLoginSessionRead])
async def get_user_login_sessions(
    principal: AuthenticatedPrincipalDependency,
    db_session: DatabaseSessionDependency,
    response: Response,
) -> list[UserLoginSessionRead]:
    """List the current user's bounded browser-session history."""

    _set_no_store(response)
    return await list_user_login_sessions(
        db_session,
        user_id=principal.user_id,
        current_login_session_id=principal.login_session_id,
    )


@router.delete("/sessions/{login_session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user_login_session(
    login_session_id: str,
    request: Request,
    principal: AuthenticatedPrincipalDependency,
    db_session: DatabaseSessionDependency,
    settings: SettingsDependency,
) -> Response:
    """Revoke one owned browser session while hiding cross-user session identifiers."""

    current_session = await require_owned_login_session(
        db_session,
        user_id=principal.user_id,
        login_session_id=principal.login_session_id,
    )
    _validate_request_csrf(
        request,
        login_session=current_session,
        settings=settings,
    )
    target_session = await require_owned_login_session(
        db_session,
        user_id=principal.user_id,
        login_session_id=login_session_id,
    )
    await revoke_login_session(
        db_session,
        login_session=target_session,
        reason="user_session_revoke",
    )
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    _set_no_store(response)
    if target_session.login_session_id == principal.login_session_id:
        _clear_authentication_cookies(response, settings)
    return response


@router.post("/sessions/revoke-all", status_code=status.HTTP_204_NO_CONTENT)
async def post_revoke_all_user_login_sessions(
    request: Request,
    principal: AuthenticatedPrincipalDependency,
    db_session: DatabaseSessionDependency,
    settings: SettingsDependency,
) -> Response:
    """Revoke every session for the current user and log the browser out."""

    current_session = await require_owned_login_session(
        db_session,
        user_id=principal.user_id,
        login_session_id=principal.login_session_id,
    )
    _validate_request_csrf(
        request,
        login_session=current_session,
        settings=settings,
    )
    await revoke_all_user_login_sessions(
        db_session,
        user_id=principal.user_id,
        actor_login_session_id=principal.login_session_id,
    )
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    _set_no_store(response)
    _clear_authentication_cookies(response, settings)
    return response


@router.post("/password/change", status_code=status.HTTP_204_NO_CONTENT)
async def post_change_local_password(
    payload: PasswordChangeRequest,
    request: Request,
    principal: AuthenticatedPrincipalDependency,
    db_session: DatabaseSessionDependency,
    settings: SettingsDependency,
) -> Response:
    """Change the local password after re-verification and revoke other sessions."""

    current_session = await require_owned_login_session(
        db_session,
        user_id=principal.user_id,
        login_session_id=principal.login_session_id,
    )
    _validate_request_csrf(
        request,
        login_session=current_session,
        settings=settings,
    )
    await change_local_password(
        db_session,
        current_login_session=current_session,
        current_password=payload.current_password,
        new_password=payload.new_password,
    )
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    _set_no_store(response)
    return response
