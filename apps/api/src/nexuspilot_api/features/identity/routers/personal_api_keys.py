"""Browser-only personal credential management; personal and App keys cannot manage keys."""

from fastapi import APIRouter, Query, Request, Response
from sqlalchemy import select

from nexuspilot_api.core.config import get_settings
from nexuspilot_api.core.errors import ResourceNotFoundError
from nexuspilot_api.features.identity.dependencies import AuthenticatedPrincipalDependency
from nexuspilot_api.features.identity.models.personal_api_key import UserApiKey
from nexuspilot_api.features.identity.routers.authentication import _validate_request_csrf
from nexuspilot_api.features.identity.services.authentication_service import (
    require_owned_login_session,
)
from nexuspilot_api.features.identity.services.personal_api_keys import (
    PersonalKeyCreate,
    PersonalKeyCreated,
    PersonalKeyRead,
    create_personal_key,
)
from nexuspilot_api.models.base import utc_now
from nexuspilot_api.routers.common import DatabaseSessionDependency

router = APIRouter(prefix="/api/v1/me/api-keys", tags=["personal-api-keys"])


async def check_key_mutation(request, db, principal):
    """Require the same Origin and session-bound CSRF proof as existing password mutations."""
    session = await require_owned_login_session(
        db, user_id=principal.user_id, login_session_id=principal.login_session_id
    )
    _validate_request_csrf(request, login_session=session, settings=get_settings())


@router.post("", response_model=PersonalKeyCreated, status_code=201)
async def post_personal_key(
    payload: PersonalKeyCreate,
    request: Request,
    response: Response,
    principal: AuthenticatedPrincipalDependency,
    db: DatabaseSessionDependency,
):
    """Create a user-owned key and prevent caching its one-time plaintext response."""
    await check_key_mutation(request, db, principal)
    response.headers["Cache-Control"] = "no-store"
    return await create_personal_key(db, principal.user_id, payload)


@router.get("", response_model=list[PersonalKeyRead])
async def list_personal_keys(
    response: Response,
    principal: AuthenticatedPrincipalDependency,
    db: DatabaseSessionDependency,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
):
    """Page only the logged-in owner's credential metadata, including revocation history."""
    response.headers["Cache-Control"] = "no-store"
    return list(
        await db.scalars(
            select(UserApiKey)
            .where(UserApiKey.user_id == principal.user_id)
            .order_by(UserApiKey.created_at.desc(), UserApiKey.api_key_id)
            .offset(offset)
            .limit(limit)
        )
    )


@router.delete("/{api_key_id}", status_code=204)
async def revoke_personal_key(
    api_key_id: str,
    request: Request,
    principal: AuthenticatedPrincipalDependency,
    db: DatabaseSessionDependency,
):
    """Idempotently revoke an owned key; do not cancel tasks already in flight."""
    await check_key_mutation(request, db, principal)
    row = await db.scalar(
        select(UserApiKey)
        .where(UserApiKey.api_key_id == api_key_id, UserApiKey.user_id == principal.user_id)
        .with_for_update()
    )
    if row is None:
        raise ResourceNotFoundError("Personal API key")
    row.revoked_at = row.revoked_at or utc_now()
    await db.commit()
    return Response(status_code=204, headers={"Cache-Control": "no-store"})
