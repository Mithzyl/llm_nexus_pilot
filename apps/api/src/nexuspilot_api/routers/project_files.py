"""Personal project files and conversations; Memory administration remains internal."""

from urllib.parse import quote

from fastapi import APIRouter, Response

from nexuspilot_api.routers.common import DatabaseSessionDependency, ObjectStorageDependency
from nexuspilot_api.routers.user_files import Principal
from nexuspilot_api.schemas.project_files import (
    ProjectAttachmentCreate,
    ProjectFileRead,
    ProjectFileSave,
    ProjectSessionCreate,
)
from nexuspilot_api.schemas.sessions import SessionCreate, SessionRead
from nexuspilot_api.schemas.user_files import UserFileRead
from nexuspilot_api.services.project_workspace_service import (
    list_owned_project_files,
    list_owned_project_sessions,
    materialize_saved_file,
    owned_project,
    owned_project_file,
    prepare_project_attachment,
    project_file_bytes,
    save_project_file,
)
from nexuspilot_api.services.session_service import create_session

router = APIRouter(prefix="/projects/{project_id}", tags=["project workspace"])


@router.get("/files", response_model=list[ProjectFileRead])
async def list_project_files(project_id: str, db: DatabaseSessionDependency, principal: Principal):
    """Delegate the bounded owner-checked list, including interrupted publications."""
    return await list_owned_project_files(db, project_id, principal.user_id)


@router.post("/files", response_model=ProjectFileRead, status_code=201)
async def post_project_file(
    project_id: str,
    payload: ProjectFileSave,
    db: DatabaseSessionDependency,
    storage: ObjectStorageDependency,
    principal: Principal,
):
    """Explicitly save one owned upload or artifact as an independent immutable project copy."""
    return await save_project_file(db, storage, project_id, principal.user_id, payload)


@router.post("/files/{project_file_id}/attachments", response_model=UserFileRead, status_code=201)
async def attach_project_file(
    project_id: str,
    project_file_id: str,
    payload: ProjectAttachmentCreate,
    db: DatabaseSessionDependency,
    storage: ObjectStorageDependency,
    principal: Principal,
):
    """Prepare an explicitly selected saved copy for the owner's project conversation."""
    return await prepare_project_attachment(
        db,
        storage,
        project_id,
        project_file_id,
        principal.user_id,
        payload,
    )


@router.get("/files/{project_file_id}/content")
async def download_project_file(
    project_id: str,
    project_file_id: str,
    db: DatabaseSessionDependency,
    storage: ObjectStorageDependency,
    principal: Principal,
):
    """Return verified snapshot bytes, never executable same-origin previews or storage URLs."""
    entry = await owned_project_file(db, project_id, project_file_id, principal.user_id)
    content = await project_file_bytes(storage, entry)
    return Response(
        content,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(entry.filename, safe='')}",
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        },
    )


@router.post("/files/{project_file_id}/materialize")
async def materialize_project_file(
    project_id: str,
    project_file_id: str,
    db: DatabaseSessionDependency,
    storage: ObjectStorageDependency,
    principal: Principal,
):
    """Create an immutable local working copy only under the configured server root."""
    entry = await owned_project_file(db, project_id, project_file_id, principal.user_id)
    await materialize_saved_file(storage, entry)
    return {"project_file_id": entry.project_file_id, "status": "materialized"}


@router.post("/sessions", response_model=SessionRead, status_code=201)
async def post_project_session(
    project_id: str,
    payload: ProjectSessionCreate,
    db: DatabaseSessionDependency,
    principal: Principal,
):
    """Create a retry-stable project conversation without accepting a browser-selected user."""
    await owned_project(db, project_id, principal.user_id)
    return await create_session(
        db, SessionCreate(user_id=principal.user_id, project_id=project_id, **payload.model_dump())
    )


@router.get("/sessions", response_model=list[SessionRead])
async def list_project_sessions(
    project_id: str, db: DatabaseSessionDependency, principal: Principal, offset: int = 0
):
    """Delegate bounded conversation lookup without reading unrelated histories."""
    return await list_owned_project_sessions(db, project_id, principal.user_id, offset)
