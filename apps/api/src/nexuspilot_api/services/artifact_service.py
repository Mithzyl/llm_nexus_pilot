"""Artifact validation, object persistence, and metadata transactions."""

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from nexuspilot_api.infrastructure.object_storage import ObjectStorage
from nexuspilot_api.models import LlmArtifact, new_id
from nexuspilot_api.services.lookups import require_run, require_task


async def create_artifact(
    *,
    session: AsyncSession,
    storage: ObjectStorage,
    run_id: str,
    task_id: str | None,
    artifact_type: str,
    filename: str,
    content: bytes,
    content_type: str,
) -> LlmArtifact:
    """Store artifact bytes and persist searchable metadata under the owning run."""

    await require_run(session, run_id)
    if task_id:
        task = await require_task(session, task_id)
        if task.run_id != run_id:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Task does not belong to the run",
            )
    artifact_id = new_id()
    artifact = LlmArtifact(
        artifact_id=artifact_id,
        run_id=run_id,
        task_id=task_id,
        artifact_type=artifact_type,
        filename=filename,
        mime_type=content_type,
        content_hash="pending",
        storage_uri=f"pending://{run_id}/{artifact_id}",
        size_bytes=len(content),
    )
    session.add(artifact)
    await session.flush()
    stored = await storage.put_bytes(
        f"{run_id}/{artifact.artifact_id}/{filename}",
        content,
        content_type,
    )
    artifact.content_hash = stored.content_hash
    artifact.storage_uri = stored.uri
    artifact.size_bytes = stored.size_bytes
    await session.commit()
    await session.refresh(artifact)
    return artifact
