"""Explicit account linking with no implicit resource transfer or memory permission."""

import secrets
from datetime import UTC, timedelta

from sqlalchemy import or_, select, update

from nexuspilot_api.core.errors import InvalidRequestError, ResourceConflictError
from nexuspilot_api.features.identity.models.account_linking import (
    AccountLinkRequest,
    EcosystemAccountLink,
)
from nexuspilot_api.features.identity.models.applications import Application, ApplicationUserMapping
from nexuspilot_api.features.identity.services.authentication_service import (
    authentication_token_hash,
)
from nexuspilot_api.features.identity.services.sso import live_user_session
from nexuspilot_api.models.base import utc_now
from nexuspilot_api.models.identity import User


async def live_mapping(db, mapping_id):
    """Recheck the saved App identity and owner, rather than accepting a supplied target owner."""
    mapping = await db.get(ApplicationUserMapping, mapping_id, populate_existing=True)
    app = (
        await db.get(Application, mapping.application_id, populate_existing=True)
        if mapping
        else None
    )
    user = (
        await db.get(User, mapping.pilot_principal_id, populate_existing=True) if mapping else None
    )
    if (
        not mapping
        or not mapping.is_enabled
        or not app
        or not app.is_enabled
        or not user
        or not user.is_active
    ):
        raise InvalidRequestError("Application association is unavailable")
    return mapping


async def begin_link(db, *, mapping_id):
    """Start ten-minute consent after callers authenticate the external App user."""
    await live_mapping(db, mapping_id)
    ticket = secrets.token_urlsafe(32)
    db.add(
        AccountLinkRequest(
            mapping_id=mapping_id,
            ticket_digest=authentication_token_hash(ticket),
            app_verified_at=utc_now(),
            expires_at=utc_now() + timedelta(minutes=10),
        )
    )
    await db.commit()
    return ticket


async def confirm_link(db, *, ticket, owner, session_id):
    """Consume both-party approval once, serializing conflicting pairs without changing mappings."""
    request = await db.scalar(
        select(AccountLinkRequest)
        .where(AccountLinkRequest.ticket_digest == authentication_token_hash(ticket))
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if not request or request.consumed_at or request.expires_at.replace(tzinfo=UTC) <= utc_now():
        raise InvalidRequestError("Invalid account link request")
    mapping = await live_mapping(db, request.mapping_id)
    if owner == mapping.pilot_principal_id:
        raise ResourceConflictError("Accounts already have the same owner")
    # Sorted principal locks serialize simultaneous reverse/cross-App associations.
    await db.scalars(
        select(User)
        .where(User.user_id.in_([owner, mapping.pilot_principal_id]))
        .order_by(User.user_id)
        .with_for_update()
    )
    await live_user_session(db, owner, session_id)
    await db.scalar(
        select(Application)
        .where(Application.application_id == mapping.application_id)
        .with_for_update()
    )
    conflicts = list(
        await db.scalars(
            select(EcosystemAccountLink)
            .where(
                EcosystemAccountLink.status == "active",
                or_(
                    EcosystemAccountLink.primary_principal_id.in_(
                        [owner, mapping.pilot_principal_id]
                    ),
                    EcosystemAccountLink.linked_principal_id.in_(
                        [owner, mapping.pilot_principal_id]
                    ),
                ),
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    )
    for other in conflicts:
        if (
            other.linked_principal_id in {owner, mapping.pilot_principal_id}
            or other.primary_principal_id == mapping.pilot_principal_id
        ):
            raise ResourceConflictError("Account is already linked")
        other_mapping = await db.get(ApplicationUserMapping, other.mapping_id)
        if other_mapping.application_id == mapping.application_id:
            raise ResourceConflictError("An account for this application is already linked")
    direct = await db.scalar(
        select(ApplicationUserMapping).where(
            ApplicationUserMapping.application_id == mapping.application_id,
            ApplicationUserMapping.pilot_principal_id == owner,
        )
    )
    if direct:
        raise ResourceConflictError("An account for this application is already associated")
    link = await db.scalar(
        select(EcosystemAccountLink)
        .where(EcosystemAccountLink.mapping_id == mapping.mapping_id)
        .with_for_update()
    )
    now = utc_now()
    if link is None:
        link = EcosystemAccountLink(mapping_id=mapping.mapping_id, version=1)
        db.add(link)
    else:
        link.version += 1
    link.primary_principal_id, link.linked_principal_id = owner, mapping.pilot_principal_id
    link.status, link.revoked_at = "active", None
    link.app_verified_at = request.app_verified_at
    link.browser_verified_at = link.verified_at = now
    request.consumed_at = now
    await db.commit()
    return link


async def linked_mapping(db, *, owner, app_id):
    """Resolve a live linked App account for login, never for implicit data access."""
    row = await db.scalar(
        select(EcosystemAccountLink)
        .join(ApplicationUserMapping)
        .where(
            EcosystemAccountLink.primary_principal_id == owner,
            EcosystemAccountLink.status == "active",
            ApplicationUserMapping.application_id == app_id,
        )
        .execution_options(populate_existing=True)
    )
    if row is None:
        return None
    mapping = await live_mapping(db, row.mapping_id)
    if mapping.pilot_principal_id != row.linked_principal_id:
        raise InvalidRequestError("Application association changed")
    return mapping


async def revoke_link(db, *, link_id, owner, mapping_id=None):
    """Revoke for either verified owner and invalidate dependent shares in one commit."""
    from nexuspilot_api.features.memory.models.product_memory import UserMemoryShareGrant

    row = await db.scalar(
        select(EcosystemAccountLink)
        .where(
            EcosystemAccountLink.link_id == link_id,
            or_(
                EcosystemAccountLink.primary_principal_id == owner,
                EcosystemAccountLink.linked_principal_id == owner,
            ),
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is None or (mapping_id is not None and row.mapping_id != mapping_id):
        raise InvalidRequestError("Account link not found")
    if row.status == "active":
        row.status, row.revoked_at = "revoked", utc_now()
        row.version += 1
        await db.execute(
            update(UserMemoryShareGrant)
            .where(
                or_(
                    UserMemoryShareGrant.account_link_id == row.link_id,
                    UserMemoryShareGrant.source_account_link_id == row.link_id,
                ),
                UserMemoryShareGrant.revoked_at.is_(None),
            )
            .values(revoked_at=utc_now(), version=UserMemoryShareGrant.version + 1)
        )
        await db.commit()
    return row
