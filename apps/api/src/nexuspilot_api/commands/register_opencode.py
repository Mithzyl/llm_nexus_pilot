"""Register the documented OpenCode model in local MySQL without credentials or HTTP calls.

Run with the API environment: python -m nexuspilot_api.commands.register_opencode
Requires existing model/endpoint tables. Refuses nonlocal databases and conflicting
enabled URLs. Uses additive metadata, preserving all other providers and models.
"""

import asyncio
from urllib.parse import urlsplit

from sqlalchemy import select

from nexuspilot_api.core.config import get_settings
from nexuspilot_api.infrastructure.database import database_session_factory, engine
from nexuspilot_api.models import LlmModelCatalogVersion, LlmProviderEndpoint, LlmRegisteredModel
from nexuspilot_api.models.base import utc_now

BASE_URL = "https://opencode.ai/inference/go/openai/v1"
MODEL = "deepseek-v4.1-flash"


async def register():
    """Add an enabled endpoint, model identity and sourced capability snapshot atomically."""
    settings = get_settings()
    if settings.environment != "development" or urlsplit(settings.database_url).hostname not in {
        "localhost",
        "127.0.0.1",
    }:
        raise RuntimeError("Local development database required")
    async with database_session_factory() as db:
        endpoint = await db.scalar(
            select(LlmProviderEndpoint).where(
                LlmProviderEndpoint.provider == "opencode", LlmProviderEndpoint.enabled.is_(True)
            )
        )
        if endpoint and endpoint.base_url != BASE_URL:
            raise RuntimeError("Existing enabled OpenCode URL differs; review before replacing")
        if endpoint is None:
            db.add(LlmProviderEndpoint(provider="opencode", base_url=BASE_URL, enabled=True))
        model = await db.scalar(
            select(LlmRegisteredModel).where(
                LlmRegisteredModel.provider == "opencode",
                LlmRegisteredModel.model_identifier == MODEL,
            )
        )
        if model is None:
            db.add(
                LlmRegisteredModel(
                    provider="opencode",
                    model_identifier=MODEL,
                    display_name="DeepSeek V4.1 Flash (OpenCode)",
                    metadata_json={"source": "https://opencode.ai/v2/docs/console/models/"},
                    reasoning_supported_efforts_json=[],
                    reasoning_effort_mapping_json={},
                    reasoning_supports_disable=False,
                    reasoning_profile_version="provider-default.v1",
                )
            )
        version = "opencode-models-dev-20261023"
        existing = await db.scalar(
            select(LlmModelCatalogVersion).where(
                LlmModelCatalogVersion.provider == "opencode",
                LlmModelCatalogVersion.model == MODEL,
                LlmModelCatalogVersion.catalog_version == version,
            )
        )
        if existing is None:
            db.add(
                LlmModelCatalogVersion(
                    provider="opencode",
                    model=MODEL,
                    catalog_version=version,
                    source="https://models.dev/api.json",
                    confirmed_at=utc_now(),
                    context_window=1_000_000,
                    supports_tools=True,
                    supports_streaming=True,
                    supports_caching=True,
                    supports_structured_output=False,
                    supports_vision=False,
                    tokenizer_name="cl100k_base_proxy",
                    tokenizer_version="v1",
                    capabilities_json={
                        "metadata_source": "models.dev/opencode/deepseek-v4.1-flash",
                        "reasoning_controls": "provider_default",
                        "native_json_schema": "not_enabled",
                        "live_generation_verified": False,
                    },
                )
            )
        await db.commit()
    print("Registered opencode/deepseek-v4.1-flash; no provider request sent")


async def main():
    """Release local database resources even if registration is rejected."""
    try:
        await register()
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
