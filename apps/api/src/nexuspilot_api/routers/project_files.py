"""Personal project files and conversations; Memory administration remains internal."""

from urllib.parse import quote

from fastapi import APIRouter, Response
from sqlalchemy import select

from nexuspilot_api.models import LlmSession
from nexuspilot_api.models.project_file import ProjectFile
from nexuspilot_api.routers.common import DatabaseSessionDependency, ObjectStorageDependency
from nexuspilot_api.routers.user_files import Principal
from nexuspilot_api.schemas.project_files import (
    ProjectFileRead,
    ProjectFileSave,
    ProjectSessionCreate,
)
from nexuspilot_api.schemas.sessions import SessionCreate, SessionRead
from nexuspilot_api.services.project_workspace_service import (
    materialize_saved_file,
    owned_project,
    owned_project_file,
    project_file_bytes,
    save_project_file,
)
from nexuspilot_api.services.session_service import create_session

router = APIRouter(prefix="/projects/{project_id}", tags=["project workspace"])


@router.get("/files", response_model=list[ProjectFileRead])
async def list_project_files(project_id: str, db: DatabaseSessionDependency, principal: Principal):
    """List bounded saved copies, including interrupted publications available for retry."""
    await owned_project(db, project_id, principal.user_id)
    return list(
        await db.scalars(
            select(ProjectFile)
            .where(ProjectFile.project_id == project_id)
            .order_by(ProjectFile.created_at, ProjectFile.project_file_id)
            .limit(500)
        )
    )


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
    """Return a bounded page of project conversations without reading unrelated histories."""
    from fastapi import HTTPException

    if offset < 0 or offset > 100000:
        raise HTTPException(422, "Invalid offset")
    await owned_project(db, project_id, principal.user_id)
    return list(
        await db.scalars(
            select(LlmSession)
            .where(LlmSession.project_id == project_id, LlmSession.user_id == principal.user_id)
            .order_by(LlmSession.created_at.desc(), LlmSession.session_id)
            .offset(offset)
            .limit(100)
        )
    )
