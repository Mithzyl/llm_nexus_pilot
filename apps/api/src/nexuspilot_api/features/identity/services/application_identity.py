"""Resolve authenticated application subjects into existing private resource owners."""

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from nexuspilot_api.core.errors import InvalidRequestError, ResourceNotFoundError
from nexuspilot_api.features.identity.models.applications import Application, ApplicationUserMapping
from nexuspilot_api.models.base import new_id
from nexuspilot_api.models.identity import User


async def resolve_application_user(db, *, application_id, external_user_id):
    """Resolve or atomically provision an owner after the caller authenticates the app.

    Uniqueness handles concurrent first requests. A revoked mapping is never replaced;
    application IDs and external subjects are exact, case-sensitive identifiers.
    """
    if (
        not isinstance(external_user_id, str)
        or not 1 <= len(external_user_id) <= 128
        or external_user_id != external_user_id.strip()
    ):
        raise InvalidRequestError("Invalid application subject")
    app = await db.get(Application, application_id, populate_existing=True)
    if app is None or not app.is_enabled:
        raise ResourceNotFoundError("Application")
    query = (
        select(ApplicationUserMapping)
        .where(
            ApplicationUserMapping.application_id == application_id,
            ApplicationUserMapping.external_user_id == external_user_id,
        )
        .execution_options(populate_existing=True)
    )
    mapping = await db.scalar(query)
    if mapping is None:
        try:
            async with db.begin_nested():
                owner = User(user_id=new_id(), display_name="Application user", is_active=True)
                db.add(owner)
                await db.flush()
                mapping = ApplicationUserMapping(
                    application_id=application_id,
                    external_user_id=external_user_id,
                    pilot_principal_id=owner.user_id,
                )
                db.add(mapping)
                await db.flush()
        except IntegrityError:
            # A locking read sees the winner under MySQL REPEATABLE READ as well.
            mapping = await db.scalar(query.with_for_update())
            if mapping is None:
                raise
        await db.commit()
    owner = await db.get(User, mapping.pilot_principal_id, populate_existing=True)
    if not mapping.is_enabled or owner is None or not owner.is_active:
        raise ResourceNotFoundError("Application user")
    return mapping
