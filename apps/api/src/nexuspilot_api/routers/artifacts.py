"""Artifact upload controller."""

from pathlib import PurePath
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status

from nexuspilot_api.core.config import get_settings
from nexuspilot_api.routers.common import ObjectStorageDependency, SessionDependency
from nexuspilot_api.schemas.artifacts import ArtifactRead
from nexuspilot_api.services.artifact_service import create_artifact

router = APIRouter(tags=["artifacts"])


@router.post(
    "/runs/{run_id}/artifacts",
    response_model=ArtifactRead,
    status_code=status.HTTP_201_CREATED,
)
async def post_artifact(
    run_id: str,
    session: SessionDependency,
    storage: ObjectStorageDependency,
    file: Annotated[UploadFile, File()],
    artifact_type: Annotated[str, Form(min_length=1, max_length=64)],
    task_id: Annotated[str | None, Form()] = None,
) -> ArtifactRead:
    """Read a bounded upload and delegate object and metadata persistence to the service."""

    filename = PurePath(file.filename or "artifact.bin").name
    max_size = get_settings().max_artifact_size_bytes
    content = await file.read(max_size + 1)
    if len(content) > max_size:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Artifact exceeds {max_size} bytes",
        )
    artifact = await create_artifact(
        session=session,
        storage=storage,
        run_id=run_id,
        task_id=task_id,
        artifact_type=artifact_type,
        filename=filename,
        content=content,
        content_type=file.content_type or "application/octet-stream",
    )
    return ArtifactRead.model_validate(artifact)
