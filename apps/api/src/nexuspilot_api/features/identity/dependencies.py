"""FastAPI dependencies that resolve the current authenticated final user."""

from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from nexuspilot_api.core.config import Settings, get_settings
from nexuspilot_api.features.identity.schemas.authentication import AuthenticatedPrincipalRead
from nexuspilot_api.features.identity.services.authentication_service import (
    require_owned_login_session,
    resolve_browser_principal,
    validate_login_session_csrf,
)
from nexuspilot_api.infrastructure.database import get_database_session

SESSION_COOKIE_NAME = "nexuspilot_session"
CSRF_COOKIE_NAME = "nexuspilot_csrf"


async def get_authenticated_principal(
    request: Request,
    db_session: Annotated[AsyncSession, Depends(get_database_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> AuthenticatedPrincipalRead:
    """Resolve the current final user from an opaque Cookie session."""

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


AuthenticatedPrincipalDependency = Annotated[
    AuthenticatedPrincipalRead,
    Depends(get_authenticated_principal),
]
