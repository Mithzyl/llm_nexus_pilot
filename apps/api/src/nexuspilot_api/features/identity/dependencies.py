"""FastAPI dependencies that resolve the current authenticated final user."""

from typing import Annotated

from fastapi import Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from nexuspilot_api.core.config import Settings, get_settings
from nexuspilot_api.core.security import require_api_key
from nexuspilot_api.features.identity.schemas.authentication import (
    AuthenticatedPrincipalRead,
    WorkspacePrincipalRead,
)
from nexuspilot_api.features.identity.services.authentication_service import (
    require_owned_login_session,
    resolve_browser_principal,
    resolve_local_workspace_principal,
    validate_login_session_csrf,
)
from nexuspilot_api.infrastructure.database import get_database_session

SESSION_COOKIE_NAME = "nexuspilot_session"
CSRF_COOKIE_NAME = "nexuspilot_csrf"


def require_browser_authentication_enabled(
    settings: Annotated[Settings, Depends(get_settings)],
) -> None:
    """Keep login and account-management endpoints unavailable in single-user local mode."""

    if not settings.authentication_enabled:
        raise HTTPException(status_code=403, detail="当前部署未启用登录。")


async def get_authenticated_principal(
    request: Request,
    db_session: Annotated[AsyncSession, Depends(get_database_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> AuthenticatedPrincipalRead:
    """Resolve an enabled login session; local owners can never manage browser credentials."""

    require_browser_authentication_enabled(settings)

    principal = await resolve_browser_principal(
        db_session,
        session_token=request.cookies.get(SESSION_COOKIE_NAME),
        authorization_header=request.headers.get("authorization"),
        settings=settings,
    )
    if request.headers.get("x-csrf-token"):
        login_session = await require_owned_login_session(
            db_session,
            user_id=principal.user_id,
            login_session_id=principal.login_session_id,
        )
        validate_login_session_csrf(
            login_session=login_session,
            csrf_cookie=request.cookies.get(CSRF_COOKIE_NAME),
            csrf_header=request.headers.get("x-csrf-token"),
        )
    return principal


async def get_workspace_principal(
    request: Request,
    db_session: Annotated[AsyncSession, Depends(get_database_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> WorkspacePrincipalRead:
    """Resolve configured access mode without falling back when enabled authentication fails."""

    if settings.authentication_enabled:
        return await get_authenticated_principal(request, db_session, settings)
    await require_api_key(request.headers.get("x-api-key"))
    return await resolve_local_workspace_principal(db_session)


AuthenticatedPrincipalDependency = Annotated[
    AuthenticatedPrincipalRead,
    Depends(get_authenticated_principal),
]
