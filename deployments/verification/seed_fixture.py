"""Seed only a newly created disposable Compose project; password is read from stdin.

This file is not copied into production images. Local verification mounts it
read-only and runs it explicitly after migrating its own empty database.
"""

import asyncio
import json
import sys

from sqlalchemy import select

from nexuspilot_api.models import (
    LlmModelCatalogVersion,
    LlmProviderEndpoint,
    LlmRegisteredModel,
    User,
)
from nexuspilot_api.models.base import utc_now
from nexuspilot_api.features.identity.services.authentication_service import (
    provision_local_password_user,
)
from nexuspilot_api.infrastructure.database import database_session_factory, engine
from nexuspilot_models.contracts import ProviderName


async def main(password):
    """Create synthetic accounts and point the fixture model only at the private HTTP server."""
    async with database_session_factory() as db:
        if await db.scalar(select(User.user_id).limit(1)):
            raise RuntimeError("Refusing to seed a database containing users")
        for user in ("preverify-a", "preverify-b"):
            await provision_local_password_user(
                db,
                user_id=user,
                display_name=user,
                email=f"{user}@example.test",
                password=password,
            )
        endpoint = await db.scalar(
            select(LlmProviderEndpoint).where(
                LlmProviderEndpoint.provider == ProviderName.DEEPSEEK,
                LlmProviderEndpoint.enabled.is_(True),
            )
        )
        endpoint.base_url = "http://fixture-provider:8090/v1"
        model = LlmRegisteredModel(
            provider=ProviderName.DEEPSEEK,
            model_identifier="deepseek-v4-flash",
            display_name="Local verification fixture",
            reasoning_supported_efforts_json=["minimal", "medium", "xhigh"],
            reasoning_supports_disable=True,
            reasoning_default_enabled=True,
            reasoning_default_effort="medium",
            reasoning_effort_mapping_json={
                "minimal": "high",
                "medium": "high",
                "xhigh": "max",
            },
            reasoning_profile_version="fixture.v1",
        )
        db.add(model)
        await db.flush()
        db.add(
            LlmModelCatalogVersion(
                provider="deepseek",
                model="deepseek-v4-flash",
                catalog_version="local-preverification",
                source="fixture",
                confirmed_at=utc_now(),
                context_window=65536,
                supports_streaming=True,
                tokenizer_name="utf8_bytes_upper_bound",
                tokenizer_version="v1",
                capabilities_json={},
            )
        )
        await db.commit()
    await engine.dispose()
    print("Synthetic accounts and private Provider fixture initialized.")


if __name__ == "__main__":
    asyncio.run(main(json.load(sys.stdin)["password"]))
