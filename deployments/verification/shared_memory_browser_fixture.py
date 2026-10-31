"""Seed synthetic linked App memory for the isolated identity browser fixture.

Used only by serve_identity_memory_fixture.py --shared-memory. Requires its migrated
MySQL database, private encryption key and real local MinIO; never uses real App data.
"""


async def seed_shared_memory():
    """Create an App-owned sensitive memory and explicitly share only its label with Nexus."""
    from nexuspilot_api.infrastructure.database import database_session_factory, engine
    from nexuspilot_api.infrastructure.object_storage import get_object_storage
    from nexuspilot_api.features.identity.models.applications import Application
    from nexuspilot_api.features.identity.models.account_linking import EcosystemAccountLink
    from nexuspilot_api.features.identity.services.application_identity import resolve_application_user
    from nexuspilot_api.features.memory.services.product_memory import MemoryService
    from nexuspilot_api.features.memory.services.sharing import grant_share
    from nexuspilot_api.models.base import utc_now

    async with database_session_factory() as db:
        db.add(Application(application_id="personal_assistant"))
        await db.commit()
        mapping = await resolve_application_user(db, application_id="personal_assistant",
                                                 external_user_id="shared-fixture-app")
        db.add(EcosystemAccountLink(primary_principal_id="shared-fixture-user",
            linked_principal_id=mapping.pilot_principal_id, mapping_id=mapping.mapping_id,
            status="active", app_verified_at=utc_now(), browser_verified_at=utc_now(),
            verified_at=utc_now()))
        await db.commit()
        source = MemoryService(db, get_object_storage(), mapping.pilot_principal_id, "personal_assistant")
        saved = await source.mutate({"action":"save", "key":"browser-shared-source",
            "text":"Unshared synthetic private address", "typed_data":{
                "memory_type":"place", "label":"SHARED_BROWSER_HOME",
                "address":"NEVER_DISCLOSE_ADDRESS", "coordinate_system":"GCJ02"},
            "confirmed_type":True, "confirmed_sensitive":True})
        await grant_share(source, entry_id=saved["entry_id"], expected_version=1,
            target_entry="native", fields=["typed_data.label"], automatic=True)
    await engine.dispose()
