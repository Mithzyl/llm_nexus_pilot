"""Nexus browser and authenticated App endpoints for explicit account association."""

from datetime import UTC

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import or_, select

from nexuspilot_api.core.config import get_settings
from nexuspilot_api.core.errors import InvalidRequestError
from nexuspilot_api.features.identity.models.account_linking import (
    AccountLinkRequest,
    EcosystemAccountLink,
)
from nexuspilot_api.features.identity.models.applications import ApplicationUserMapping
from nexuspilot_api.features.identity.routers.application_runs import ApplicationIdentity
from nexuspilot_api.features.identity.services.account_linking import (
    begin_link,
    confirm_link,
    live_mapping,
    revoke_link,
)
from nexuspilot_api.features.identity.services.authentication_service import (
    authentication_token_hash,
    resolve_browser_principal,
)
from nexuspilot_api.models.base import utc_now
from nexuspilot_api.routers.common import DatabaseSessionDependency

browser_router = APIRouter(tags=["account-links"])
app_router = APIRouter(prefix="/api/v1/app", tags=["application-account-links"])


class LinkTicket(BaseModel):
    """Accept only the App-issued opaque consent ticket, never a selected owner."""

    model_config = ConfigDict(extra="forbid")
    ticket: str = Field(min_length=32, max_length=128)


class RevokeLink(BaseModel):
    """Select an association; caller identity must be independently authenticated."""

    model_config = ConfigDict(extra="forbid")
    link_id: str = Field(min_length=36, max_length=36)


async def browser_owner(request, db):
    """Reject development and personal-key identities for browser account consent."""
    return await resolve_browser_principal(
        db,
        session_token=request.cookies.get("nexuspilot_session"),
        authorization_header=request.headers.get("authorization"),
        settings=get_settings(),
    )


@app_router.post("/me/account-link-requests")
async def start_account_link(
    identity: ApplicationIdentity, db: DatabaseSessionDependency, response: Response
):
    """Treat the App server request as its user's explicit confirmation, not a grant to data."""
    response.headers["Cache-Control"] = "no-store"
    return {"ticket": await begin_link(db, mapping_id=identity.mapping_id), "expires_in": 600}


@browser_router.post("/me/account-links/preview")
async def preview_account_link(
    payload: LinkTicket, request: Request, response: Response, db: DatabaseSessionDependency
):
    """Show the exact App identity being linked before a separate explicit confirmation."""
    await browser_owner(request, db)
    row = await db.scalar(
        select(AccountLinkRequest).where(
            AccountLinkRequest.ticket_digest == authentication_token_hash(payload.ticket)
        )
    )
    if not row or row.consumed_at or row.expires_at.replace(tzinfo=UTC) <= utc_now():
        raise InvalidRequestError("Invalid account link request")
    mapping = await live_mapping(db, row.mapping_id)
    response.headers["Cache-Control"] = "no-store"
    return {"application_id": mapping.application_id, "external_user_id": mapping.external_user_id}


@browser_router.post("/me/account-links/confirm")
async def confirm_account_link(
    payload: LinkTicket, request: Request, response: Response, db: DatabaseSessionDependency
):
    """Consume the App approval only after Nexus browser consent and CSRF validation."""
    principal = await browser_owner(request, db)
    row = await confirm_link(
        db, ticket=payload.ticket, owner=principal.user_id, session_id=principal.login_session_id
    )
    response.headers["Cache-Control"] = "no-store"
    return {"link_id": row.link_id, "status": row.status, "version": row.version}


@browser_router.get("/me/account-links")
async def list_account_links(request: Request, response: Response, db: DatabaseSessionDependency):
    """List only the browser user's own associations, without business data or credentials."""
    principal = await browser_owner(request, db)
    rows = await db.scalars(
        select(EcosystemAccountLink)
        .where(
            or_(
                EcosystemAccountLink.primary_principal_id == principal.user_id,
                EcosystemAccountLink.linked_principal_id == principal.user_id,
            )
        )
        .order_by(EcosystemAccountLink.link_id)
        .limit(100)
    )
    response.headers["Cache-Control"] = "no-store"
    items = []
    for row in rows:
        mapping = await db.get(ApplicationUserMapping, row.mapping_id)
        items.append(
            {
                "link_id": row.link_id,
                "status": row.status,
                "version": row.version,
                "application_id": mapping.application_id,
                "external_user_id": mapping.external_user_id,
            }
        )
    return {"items": items}


@browser_router.post("/me/account-links/revoke")
async def revoke_browser_account_link(
    payload: RevokeLink, request: Request, db: DatabaseSessionDependency
):
    """Revoke using the real Nexus browser identity; do not delete the App's user account."""
    principal = await browser_owner(request, db)
    row = await revoke_link(db, link_id=payload.link_id, owner=principal.user_id)
    return {"link_id": row.link_id, "status": row.status, "version": row.version}


@app_router.post("/me/account-links/revoke")
async def revoke_app_account_link(
    payload: RevokeLink, identity: ApplicationIdentity, db: DatabaseSessionDependency
):
    """Allow the independently authenticated App user to withdraw their association."""
    row = await revoke_link(
        db,
        link_id=payload.link_id,
        owner=identity.pilot_principal_id,
        mapping_id=identity.mapping_id,
    )
    return {"link_id": row.link_id, "status": row.status, "version": row.version}
