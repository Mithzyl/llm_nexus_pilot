"""Register the operator-approved Strata endpoint/model without storing any API Key.

Run inside the API environment after backing up MySQL; requires existing schema.
Only inserts absent records. Conflicting enabled addresses stop the transaction.
Restart API after setting its private NEXUSPILOT_STRATA_API_KEY and running this file.
Capability evidence: local run 07bd95b7-bf8b-4893-8c73-f77f5db2c4be on 2026-10-18.
Structured JSON reliability is not asserted; no model request is made here.
"""

import asyncio

from sqlalchemy import select

from nexuspilot_api.infrastructure.database import database_session_factory, engine
from nexuspilot_api.models import (
    LlmModelCatalogVersion,
    LlmProviderEndpoint,
    LlmRegisteredModel,
)
from nexuspilot_api.models.base import utc_now

MODEL = "tiel-coder-35b-a3b-ud-q4_k_xl"
URL = "https://mithfusion.site/v1"
VERSION = "strata.file-verification.20261018"


async def register():
    """Insert missing endpoint, model and verified tool capability in one SQL transaction."""
    async with database_session_factory() as db:
        endpoint = await db.scalar(
            select(LlmProviderEndpoint).where(
                LlmProviderEndpoint.provider == "strata",
                LlmProviderEndpoint.enabled.is_(True),
            )
        )
        if endpoint is not None and endpoint.base_url != URL:
            raise RuntimeError(
                "Existing enabled Strata address differs; no changes applied"
            )
        if endpoint is None:
            db.add(LlmProviderEndpoint(provider="strata", base_url=URL, enabled=True))
        model = await db.scalar(
            select(LlmRegisteredModel).where(
                LlmRegisteredModel.provider == "strata",
                LlmRegisteredModel.model_identifier == MODEL,
            )
        )
        if model is None:
            db.add(
                LlmRegisteredModel(
                    provider="strata",
                    model_identifier=MODEL,
                    display_name="Strata Tiel Coder 35B",
                    reasoning_supported_efforts_json=[],
                    reasoning_supports_disable=False,
                    reasoning_default_enabled=True,
                    reasoning_default_effort="medium",
                    reasoning_profile_version="strata.provider-default.v1",
                )
            )
        catalog = await db.scalar(
            select(LlmModelCatalogVersion).where(
                LlmModelCatalogVersion.provider == "strata",
                LlmModelCatalogVersion.model == MODEL,
                LlmModelCatalogVersion.catalog_version == VERSION,
            )
        )
        if catalog is None:
            db.add(
                LlmModelCatalogVersion(
                    provider="strata",
                    model=MODEL,
                    catalog_version=VERSION,
                    source="GET /v1/models meta.n_ctx; 20261018 real file-tool acceptance",
                    confirmed_at=utc_now(),
                    context_window=163840,
                    supports_tools=True,
                    supports_structured_output=False,
                    supports_vision=False,
                    supports_caching=True,
                    supports_streaming=True,
                    tokenizer_name="utf8_bytes_upper_bound",
                    tokenizer_version="v1",
                    capabilities_json={},
                )
            )
        await db.commit()
        print(
            "Strata endpoint/model registered; API Key remains in private environment"
        )


async def main():
    """Release database connections after successful provisioning or a rejected conflict."""
    try:
        await register()
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
