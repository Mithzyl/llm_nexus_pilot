"""Run-produced Artifact metadata and object-content controller."""

from pathlib import PurePosixPath
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import StreamingResponse

from nexuspilot_api.core.config import get_settings
from nexuspilot_api.routers.common import (
    CursorCodecDependency,
    DatabaseSessionDependency,
    ObjectStorageDependency,
)
from nexuspilot_api.schemas.pagination import CursorPage
from nexuspilot_api.schemas.run_artifacts import (
    RunArtifactDetail,
    RunArtifactRead,
    RunArtifactSummary,
)
from nexuspilot_api.services.run_artifact_service import (
    create_run_artifact,
    get_run_artifact,
    list_run_artifacts,
    open_run_artifact_content,
)

router = APIRouter(tags=["run-artifacts"])


@router.get("/artifacts", response_model=CursorPage[RunArtifactSummary])
async def get_run_artifacts(
    db_session: DatabaseSessionDependency,
    cursor_codec: CursorCodecDependency,
    cursor: Annotated[str | None, Query(max_length=2048)] = None,
    run_id: Annotated[str | None, Query(max_length=36)] = None,
    task_id: Annotated[str | None, Query(max_length=36)] = None,
    artifact_type: Annotated[str | None, Query(min_length=1, max_length=64)] = None,
    mime_type: Annotated[str | None, Query(min_length=1, max_length=255)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> CursorPage[RunArtifactSummary]:
    """List owner-scoped Run Artifact metadata without object-storage locations."""

    return await list_run_artifacts(
        db_session,
        codec=cursor_codec,
        cursor=cursor,
        run_id=run_id,
        task_id=task_id,
        artifact_type=artifact_type,
        mime_type=mime_type,
        limit=limit,
    )


@router.get("/artifacts/{artifact_id}", response_model=RunArtifactDetail)
async def get_run_artifact_detail(
    artifact_id: str,
    db_session: DatabaseSessionDependency,
) -> RunArtifactDetail:
    """Return public metadata for one Artifact without its internal storage URI."""

    return await get_run_artifact(db_session, artifact_id)


@router.get("/artifacts/{artifact_id}/content", response_model=None)
async def get_run_artifact_content(
    artifact_id: str,
    db_session: DatabaseSessionDependency,
    storage: ObjectStorageDependency,
) -> StreamingResponse:
    """Proxy a size-validated Artifact stream without exposing MinIO coordinates."""

    run_artifact_download = await open_run_artifact_content(
        db_session=db_session,
        storage=storage,
        artifact_id=artifact_id,
    )
    encoded_filename = quote(run_artifact_download.run_artifact.filename, safe="")
    return StreamingResponse(
        run_artifact_download.object_content.chunks,
        media_type=run_artifact_download.run_artifact.mime_type,
        headers={
            "Content-Length": str(run_artifact_download.run_artifact.size_bytes),
            "Content-Disposition": f"attachment; filename*=UTF-8''{encoded_filename}",
            "Cache-Control": "private, no-store",
            "X-Content-SHA256": run_artifact_download.run_artifact.content_hash,
        },
    )


def sanitize_run_artifact_filename(filename: str | None) -> str:
    """Return a basename for POSIX or Windows-style upload paths."""

    normalized = (filename or "artifact.bin").replace("\\", "/")
    return PurePosixPath(normalized).name or "artifact.bin"


@router.post(
    "/runs/{run_id}/artifacts",
    response_model=RunArtifactRead,
    status_code=status.HTTP_201_CREATED,
)
async def post_run_artifact(
    run_id: str,
    db_session: DatabaseSessionDependency,
    storage: ObjectStorageDependency,
    file: Annotated[UploadFile, File()],
    artifact_type: Annotated[str, Form(min_length=1, max_length=64)],
    task_id: Annotated[str | None, Form()] = None,
) -> RunArtifactRead:
    """Read a bounded upload and delegate object and metadata persistence to the service."""

    filename = sanitize_run_artifact_filename(file.filename)
    max_size = get_settings().max_artifact_size_bytes
    uploaded_artifact_content = await file.read(max_size + 1)
    if len(uploaded_artifact_content) > max_size:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Artifact exceeds {max_size} bytes",
        )
    run_artifact = await create_run_artifact(
        db_session=db_session,
        storage=storage,
        run_id=run_id,
        task_id=task_id,
        artifact_type=artifact_type,
        filename=filename,
        artifact_content=uploaded_artifact_content,
        content_type=file.content_type or "application/octet-stream",
    )
    return RunArtifactRead.model_validate(run_artifact)
