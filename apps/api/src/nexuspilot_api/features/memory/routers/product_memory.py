"""Authenticated Run-bound personal and Nexus project memory management endpoints."""

from typing import Literal

from fastapi import APIRouter, HTTPException, Query

from nexuspilot_api.features.memory.schemas.product_memory import MemoryCommand
from nexuspilot_api.features.memory.services.product_memory import MemoryFailure, memory_for_run
from nexuspilot_api.routers.common import DatabaseSessionDependency, ObjectStorageDependency

router = APIRouter(tags=["product-memory"])


async def request_memory_service(db, storage, run_id, scope):
    """Reject use of a different entry's Run even when an owner could otherwise match."""
    service = await memory_for_run(db, storage, run_id, scope)
    if service.entry != db.info.get("memory_api_entry", "native"):
        raise MemoryFailure("memory_not_allowed")
    return service


def memory_http_error(error):
    """Map safe service codes without exposing content or private storage configuration."""
    code = str(error)
    status = 409
    if code in {"memory_not_allowed", "memory_project_not_allowed", "memory_not_found"}:
        status = 404
    elif code in {
        "memory_key_unavailable",
        "memory_storage_unavailable",
        "memory_content_unavailable",
    }:
        status = 503
    return HTTPException(status, {"code": code})


@router.get("/runs/{run_id}/memory-settings")
async def get_settings(
    run_id: str,
    db: DatabaseSessionDependency,
    storage: ObjectStorageDependency,
    scope: Literal["personal", "project"] = "personal",
):
    """Return this entry's effective setting without creating database records."""
    try:
        return await (await request_memory_service(db, storage, run_id, scope)).settings()
    except MemoryFailure as error:
        raise memory_http_error(error) from None


@router.get("/runs/{run_id}/user-memories")
async def get_memories(
    run_id: str,
    db: DatabaseSessionDependency,
    storage: ObjectStorageDependency,
    scope: Literal["personal", "project"] = "personal",
    offset: int = Query(0, ge=0, le=10000),
    limit: int = Query(20, ge=1, le=50),
):
    """Allow explicit user management even while automatic memory use is disabled."""
    try:
        return await (await request_memory_service(db, storage, run_id, scope)).query(
            offset=offset,
            limit=limit,
        )
    except MemoryFailure as error:
        raise memory_http_error(error) from None


@router.post("/runs/{run_id}/user-memories")
async def post_memory(
    run_id: str,
    payload: MemoryCommand,
    db: DatabaseSessionDependency,
    storage: ObjectStorageDependency,
):
    """Execute an authenticated explicit user action; models cannot call this HTTP controller."""
    try:
        service = await request_memory_service(db, storage, run_id, payload.scope)
        return await service.mutate(payload.model_dump(exclude_none=True, exclude={"scope"}))
    except MemoryFailure as error:
        raise memory_http_error(error) from None
