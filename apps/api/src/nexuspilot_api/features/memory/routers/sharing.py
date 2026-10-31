"""User-operated memory sharing for native and authenticated App entry settings."""

from typing import Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict, Field

from nexuspilot_api.features.memory.routers.product_memory import (
    application_management_service,
    management_service,
    memory_http_error,
    request_memory_service,
)
from nexuspilot_api.features.memory.services.product_memory import MemoryFailure
from nexuspilot_api.features.memory.services.sharing import (
    disclose_shared,
    grant_share,
    revoke_share,
)
from nexuspilot_api.routers.common import DatabaseSessionDependency, ObjectStorageDependency

router = APIRouter(tags=["memory-sharing"])


@router.get("/me/memory-shares")
async def get_outgoing_shares(
    entry_id: str, db: DatabaseSessionDependency, storage: ObjectStorageDependency
):
    """Restore the current source entry's sharing choices after closing or refreshing settings."""
    from nexuspilot_api.features.memory.services.sharing import list_outgoing_shares

    try:
        return await list_outgoing_shares(await authenticated_service(db, storage), entry_id)
    except MemoryFailure as error:
        raise memory_http_error(error) from None


class ShareCommand(BaseModel):
    """Explicit user-approved projection, with no target owner supplied by the client."""

    model_config = ConfigDict(extra="forbid")
    entry_id: str = Field(min_length=36, max_length=36)
    expected_version: int = Field(ge=1)
    target_entry: Literal["native", "personal_assistant", "retail"]
    fields: list[str] = Field(min_length=1, max_length=32)
    automatic: bool = False


class RevokeCommand(BaseModel):
    """Identify the current grant version without exposing a new owner selection."""

    model_config = ConfigDict(extra="forbid")
    grant_id: str = Field(min_length=36, max_length=36)
    expected_version: int = Field(ge=1)


class SharedDisclosureCommand(RevokeCommand):
    """Require explicit user confirmation for this Run's current sharing fields."""

    confirmed: Literal[True]
    key: str = Field(min_length=1, max_length=128)


@router.post("/runs/{run_id}/shared-memory-disclosures")
async def post_shared_disclosure(
    run_id: str,
    payload: SharedDisclosureCommand,
    db: DatabaseSessionDependency,
    storage: ObjectStorageDependency,
):
    """Apply current-user consent, derived from the saved Run entry, not model arguments."""
    try:
        service = await request_memory_service(db, storage, run_id, "personal")
        return await disclose_shared(service, **payload.model_dump(exclude={"confirmed"}))
    except MemoryFailure as error:
        raise memory_http_error(error) from None


async def authenticated_service(db, storage):
    """Use the entry set by the enclosing native or App authentication dependency."""
    if db.info.get("memory_api_entry") == "native":
        return await management_service(db, storage)
    return await application_management_service(db, storage)


@router.post("/me/memory-shares")
async def post_share(
    payload: ShareCommand, db: DatabaseSessionDependency, storage: ObjectStorageDependency
):
    """Save an explicit field grant; models and personal API keys cannot approve it."""
    try:
        return await grant_share(await authenticated_service(db, storage), **payload.model_dump())
    except MemoryFailure as error:
        raise memory_http_error(error) from None


@router.post("/me/memory-shares/revoke")
async def post_revoke(
    payload: RevokeCommand, db: DatabaseSessionDependency, storage: ObjectStorageDependency
):
    """Revoke a source-owned grant without deleting any original memory or historical messages."""
    try:
        return await revoke_share(await authenticated_service(db, storage), **payload.model_dump())
    except MemoryFailure as error:
        raise memory_http_error(error) from None


@router.get("/me/shared-memories")
async def get_shared(
    db: DatabaseSessionDependency,
    storage: ObjectStorageDependency,
    offset: int = Query(0, ge=0, le=10000),
    limit: int = Query(20, ge=1, le=50),
):
    """Return only the fields granted to this entry; never return the source's complete summary."""
    try:
        from nexuspilot_api.features.memory.services.sharing import shared_query_page
        return await shared_query_page(
            await authenticated_service(db, storage), for_model=False, offset=offset, limit=limit
        )
    except MemoryFailure as error:
        raise memory_http_error(error) from None
