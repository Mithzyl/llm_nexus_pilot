"""Keep upload admission and standalone parser transport aligned at 20 MiB."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from nexuspilot_api.schemas.user_files import FileUploadCapabilities, UserFileCreate


def test_upload_accepts_twenty_mib_and_rejects_larger_reservations():
    """Accept the inclusive source-byte limit without disabling oversized-upload rejection."""
    payload = dict(
        filename="large.pdf",
        content_hash="a" * 64,
        size_bytes=20 * 1024 * 1024,
        idempotency_key="upload-size-check",
    )
    assert UserFileCreate(**payload).size_bytes == payload["size_bytes"]
    assert (
        FileUploadCapabilities(
            accepted_extensions=[".pdf"], executor_status="available"
        ).max_file_size_bytes
        == payload["size_bytes"]
    )
    with pytest.raises(ValidationError):
        UserFileCreate(**(payload | {"size_bytes": payload["size_bytes"] + 1}))


def test_parser_transport_accepts_base64_twenty_mib():
    """Standalone executor admission must include base64 expansion and JSON framing."""
    import runpy

    root = Path(__file__).resolve().parents[3]
    parser = runpy.run_path(str(root / "deployments/file-runtime/parse_office.py"))
    assert parser["MAX_SOURCE_BYTES"] == 20 * 1024 * 1024
    assert parser["MAX_REQUEST_BYTES"] >= ((20 * 1024 * 1024 + 2) // 3) * 4 + 1024


@pytest.mark.asyncio
async def test_strata_registration_is_idempotent_and_rejects_other_endpoint(
    test_database_session_factory,
):
    """Repeated provisioning keeps one model/version and never overwrites another address."""
    import runpy

    from sqlalchemy import select

    from nexuspilot_api.models import (
        LlmModelCatalogVersion,
        LlmProviderEndpoint,
        LlmRegisteredModel,
    )

    root = Path(__file__).resolve().parents[3]
    registration = runpy.run_path(str(root / "deployments/production/register_strata.py"))
    register = registration["register"]
    register.__globals__["database_session_factory"] = test_database_session_factory
    await register()
    await register()
    async with test_database_session_factory() as db:
        for model in (LlmProviderEndpoint, LlmRegisteredModel, LlmModelCatalogVersion):
            assert len(list(await db.scalars(select(model).where(model.provider == "strata")))) == 1
        endpoint = await db.scalar(
            select(LlmProviderEndpoint).where(LlmProviderEndpoint.provider == "strata")
        )
        endpoint.base_url = "https://other.example/v1"
        await db.commit()
    with pytest.raises(RuntimeError, match="differs"):
        await register()
