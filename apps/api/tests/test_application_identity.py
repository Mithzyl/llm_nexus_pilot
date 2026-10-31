"""Verify external application identities without real services or model requests."""

import pytest

from nexuspilot_api.core.errors import ResourceNotFoundError


async def test_one_principal_can_map_once_per_application(test_database_session_factory):
    """Support explicitly linked Apps without allowing two subjects in one App to share an owner."""
    from sqlalchemy.exc import IntegrityError

    from nexuspilot_api.features.identity.models.applications import (
        Application,
        ApplicationUserMapping,
    )
    from nexuspilot_api.models.identity import User

    async with test_database_session_factory() as db:
        db.add(User(user_id="shared-principal", display_name="Synthetic owner", is_active=True))
        db.add_all(
            [Application(application_id="personal_assistant"), Application(application_id="retail")]
        )
        await db.commit()
        for app in ("personal_assistant", "retail"):
            db.add(
                ApplicationUserMapping(
                    application_id=app,
                    external_user_id="external-1",
                    pilot_principal_id="shared-principal",
                )
            )
            await db.commit()
        with pytest.raises(IntegrityError):
            async with db.begin_nested():
                db.add(
                    ApplicationUserMapping(
                        application_id="retail",
                        external_user_id="external-2",
                        pilot_principal_id="shared-principal",
                    )
                )
                await db.flush()


async def test_mapping_is_stable_and_separates_applications(test_database_session_factory):
    """The same external ID maps once per app and never creates a browser login."""
    from sqlalchemy import select

    from nexuspilot_api.features.identity.models.applications import (
        Application,
        ApplicationUserMapping,
    )
    from nexuspilot_api.features.identity.services.application_identity import (
        resolve_application_user,
    )
    from nexuspilot_api.models import UserAuthenticationIdentity

    async with test_database_session_factory() as db:
        db.add_all([Application(application_id="retail"), Application(application_id="assistant")])
        await db.commit()
        first = await resolve_application_user(db, application_id="retail", external_user_id="u1")
        again = await resolve_application_user(db, application_id="retail", external_user_id="u1")
        other = await resolve_application_user(
            db, application_id="assistant", external_user_id="u1"
        )
        assert first.pilot_principal_id == again.pilot_principal_id
        assert first.pilot_principal_id != other.pilot_principal_id
        assert len(list(await db.scalars(select(ApplicationUserMapping)))) == 2
        assert list(await db.scalars(select(UserAuthenticationIdentity))) == []


async def test_disabled_application_or_mapping_cannot_resolve(test_database_session_factory):
    """Revoked app and user mapping deny access instead of minting a replacement owner."""
    from nexuspilot_api.features.identity.models.applications import Application
    from nexuspilot_api.features.identity.services.application_identity import (
        resolve_application_user,
    )

    async with test_database_session_factory() as db:
        app = Application(application_id="retail")
        db.add(app)
        await db.commit()
        mapping = await resolve_application_user(db, application_id="retail", external_user_id="u1")
        mapping.is_enabled = False
        await db.commit()
        with pytest.raises(ResourceNotFoundError):
            await resolve_application_user(db, application_id="retail", external_user_id="u1")
        app.is_enabled = False
        await db.commit()
        with pytest.raises(ResourceNotFoundError):
            await resolve_application_user(db, application_id="retail", external_user_id="u2")
