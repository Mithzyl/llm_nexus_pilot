"""Authenticated Run-bound personal and Nexus project memory management endpoints."""

from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request

from nexuspilot_api.features.memory.schemas.product_memory import MemoryCommand
from nexuspilot_api.features.memory.services.product_memory import (
    MemoryFailure,
    MemoryService,
    memory_for_run,
)
from nexuspilot_api.routers.common import DatabaseSessionDependency, ObjectStorageDependency

router = APIRouter(tags=["product-memory"])
management_router = APIRouter(tags=["product-memory-management"])


async def management_service(db, storage, project_id=None):
    """Use the authenticated native owner for settings without creating a Run or App mapping."""
    owner = db.info.get("authorized_user_id")
    if not owner or db.info.get("memory_api_entry") != "native":
        raise HTTPException(403, "Memory management requires the current native identity")
    service = MemoryService(db, storage, owner, "native", project_id)
    await service.authorize()
    return service


@management_router.get("/me/memory-settings")
@management_router.get("/projects/{project_id}/memory-settings")
async def get_management_settings(
    request: Request,
    db: DatabaseSessionDependency,
    storage: ObjectStorageDependency,
    project_id: str | None = None,
):
    """Return owner or owned-project settings independently of a conversation."""
    try:
        validate_management_target(request, project_id)
        return await (await management_service(db, storage, project_id)).settings()
    except MemoryFailure as error:
        raise memory_http_error(error) from None


@management_router.get("/me/user-memories")
@management_router.get("/projects/{project_id}/user-memories")
async def get_management_memories(
    request: Request,
    db: DatabaseSessionDependency,
    storage: ObjectStorageDependency,
    project_id: str | None = None,
    offset: int = Query(0, ge=0, le=10000),
    limit: int = Query(20, ge=1, le=50),
):
    """Page through explicit user management results even when automatic memory is disabled."""
    try:
        validate_management_target(request, project_id)
        return await (await management_service(db, storage, project_id)).query(
            offset=offset, limit=limit
        )
    except MemoryFailure as error:
        raise memory_http_error(error) from None


@management_router.post("/me/user-memories")
@management_router.post("/projects/{project_id}/user-memories")
async def post_management_memory(
    request: Request,
    payload: MemoryCommand,
    db: DatabaseSessionDependency,
    storage: ObjectStorageDependency,
    project_id: str | None = None,
):
    """Apply a user operation; disclosure still requires a Run and cannot be granted globally."""
    try:
        validate_management_target(request, project_id)
        if payload.scope != ("project" if project_id else "personal"):
            raise MemoryFailure("memory_project_required")
        service = await management_service(db, storage, project_id)
        return await service.mutate(payload.model_dump(exclude_none=True, exclude={"scope"}))
    except MemoryFailure as error:
        raise memory_http_error(error) from None


def validate_management_target(request, project_id):
    """Only a project path may select a project; the personal path cannot change scope via query."""
    if project_id != request.path_params.get("project_id"):
        raise HTTPException(422, "Project memory must use the project management path")


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
