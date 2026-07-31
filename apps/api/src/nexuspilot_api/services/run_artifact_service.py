"""Run-produced Artifact persistence, metadata queries, and controlled content reads."""

from dataclasses import dataclass
from datetime import UTC

from fastapi import HTTPException, status
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from nexuspilot_api.core.errors import (
    InvalidCursorError,
    InvalidRequestError,
    ResourceNotFoundError,
)
from nexuspilot_api.core.pagination import (
    CursorCodec,
    DatabaseQueryPaginationKey,
    database_query_fingerprint,
)
from nexuspilot_api.infrastructure.object_storage import (
    ObjectContent,
    ObjectStorage,
    ObjectStorageError,
    ObjectStorageIntegrityError,
    ObjectStorageInvalidURIError,
    ObjectStorageNotFoundError,
)
from nexuspilot_api.models import LlmRunArtifact, new_id
from nexuspilot_api.schemas.pagination import CursorPage
from nexuspilot_api.schemas.run_artifacts import RunArtifactDetail, RunArtifactSummary
from nexuspilot_api.services.lookups import require_run, require_task


@dataclass(frozen=True)
class RunArtifactDatabasePage:
    """Contain one Run Artifact page and its next query-bound database key."""

    items: list[LlmRunArtifact]
    next_database_key: DatabaseQueryPaginationKey | None


@dataclass(frozen=True)
class RunArtifactDownload:
    """Bundle trusted Run Artifact metadata with its validated object stream."""

    run_artifact: LlmRunArtifact
    object_content: ObjectContent


async def create_run_artifact(
    *,
    db_session: AsyncSession,
    storage: ObjectStorage,
    run_id: str,
    task_id: str | None,
    artifact_type: str,
    filename: str,
    artifact_content: bytes,
    content_type: str,
) -> LlmRunArtifact:
    """Store Run Artifact bytes and persist searchable metadata under its Run."""

    await require_run(db_session, run_id)
    if task_id:
        task = await require_task(db_session, task_id)
        if task.run_id != run_id:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Task does not belong to the run",
            )
    artifact_id = new_id()
    run_artifact = LlmRunArtifact(
        artifact_id=artifact_id,
        run_id=run_id,
        task_id=task_id,
        artifact_type=artifact_type,
        filename=filename,
        mime_type=content_type,
        content_hash="pending",
        storage_uri=f"pending://{run_id}/{artifact_id}",
        size_bytes=len(artifact_content),
    )
    db_session.add(run_artifact)
    await db_session.flush()
    try:
        stored_object = await storage.put_bytes(
            f"{run_id}/{run_artifact.artifact_id}/{filename}",
            artifact_content,
            content_type,
        )
    except ObjectStorageError as exc:
        await db_session.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Object storage unavailable",
        ) from exc
    run_artifact.content_hash = stored_object.content_hash
    run_artifact.storage_uri = stored_object.uri
    run_artifact.size_bytes = stored_object.size_bytes
    await db_session.commit()
    await db_session.refresh(run_artifact)
    return run_artifact


async def get_run_artifact(
    db_session: AsyncSession,
    artifact_id: str,
) -> RunArtifactDetail:
    """Return one Artifact's public metadata without exposing its storage URI."""

    run_artifact = await _require_run_artifact(db_session, artifact_id)
    return RunArtifactDetail(
        **RunArtifactSummary.model_validate(run_artifact).model_dump(),
        content_available=True,
    )


async def list_run_artifacts(
    db_session: AsyncSession,
    *,
    codec: CursorCodec,
    cursor: str | None,
    run_id: str | None,
    task_id: str | None,
    artifact_type: str | None,
    mime_type: str | None,
    limit: int,
) -> CursorPage[RunArtifactSummary]:
    """Return an owner-scoped Run Artifact page with query-bound pagination."""

    if run_id is None and task_id is None:
        raise InvalidRequestError("run_id or task_id is required")
    if run_id is not None:
        await require_run(db_session, run_id)
    if task_id is not None:
        task = await require_task(db_session, task_id)
        if run_id is not None and task.run_id != run_id:
            raise InvalidRequestError("Task does not belong to the run")
    query_fingerprint = database_query_fingerprint(
        "artifacts",
        {
            "run_id": run_id,
            "task_id": task_id,
            "artifact_type": artifact_type,
            "mime_type": mime_type,
        },
    )
    after_database_key = codec.decode_query(cursor) if cursor else None
    if after_database_key and after_database_key.query_fingerprint != query_fingerprint:
        raise InvalidCursorError
    database_page = await _query_run_artifact_database_page(
        db_session,
        run_id=run_id,
        task_id=task_id,
        artifact_type=artifact_type,
        mime_type=mime_type,
        after_database_key=after_database_key,
        query_fingerprint=query_fingerprint,
        limit=limit,
    )
    next_cursor = (
        codec.encode_query(database_page.next_database_key)
        if database_page.next_database_key
        else None
    )
    return CursorPage[RunArtifactSummary](
        items=[
            RunArtifactSummary.model_validate(run_artifact)
            for run_artifact in database_page.items
        ],
        next_cursor=next_cursor,
        has_more=next_cursor is not None,
        limit=limit,
    )


async def open_run_artifact_content(
    *,
    db_session: AsyncSession,
    storage: ObjectStorage,
    artifact_id: str,
) -> RunArtifactDownload:
    """Resolve an Artifact ID to a size-validated stream from configured object storage."""

    run_artifact = await _require_run_artifact(db_session, artifact_id)
    try:
        object_content = await storage.open_object(
            run_artifact.storage_uri,
            expected_size_bytes=run_artifact.size_bytes,
        )
    except (
        ObjectStorageIntegrityError,
        ObjectStorageInvalidURIError,
        ObjectStorageNotFoundError,
    ) as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Artifact content is inconsistent with stored metadata",
        ) from exc
    except ObjectStorageError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Object storage unavailable",
        ) from exc
    return RunArtifactDownload(
        run_artifact=run_artifact,
        object_content=object_content,
    )


async def _require_run_artifact(
    db_session: AsyncSession,
    artifact_id: str,
) -> LlmRunArtifact:
    """Return one Run Artifact database record or raise the stable not-found error."""

    run_artifact = await db_session.get(LlmRunArtifact, artifact_id)
    if run_artifact is None:
        raise ResourceNotFoundError("Artifact")
    return run_artifact


async def _query_run_artifact_database_page(
    db_session: AsyncSession,
    *,
    run_id: str | None,
    task_id: str | None,
    artifact_type: str | None,
    mime_type: str | None,
    after_database_key: DatabaseQueryPaginationKey | None,
    query_fingerprint: str,
    limit: int,
) -> RunArtifactDatabasePage:
    """Query one ordered Run Artifact page after an optional query-bound key."""

    statement = select(LlmRunArtifact)
    if run_id is not None:
        statement = statement.where(LlmRunArtifact.run_id == run_id)
    if task_id is not None:
        statement = statement.where(LlmRunArtifact.task_id == task_id)
    if artifact_type is not None:
        statement = statement.where(LlmRunArtifact.artifact_type == artifact_type)
    if mime_type is not None:
        statement = statement.where(LlmRunArtifact.mime_type == mime_type)
    if after_database_key is not None:
        cursor_time = after_database_key.created_at.astimezone(UTC).replace(tzinfo=None)
        statement = statement.where(
            or_(
                LlmRunArtifact.created_at > cursor_time,
                and_(
                    LlmRunArtifact.created_at == cursor_time,
                    LlmRunArtifact.artifact_id > after_database_key.identifier,
                ),
            )
        )
    statement = statement.order_by(
        LlmRunArtifact.created_at,
        LlmRunArtifact.artifact_id,
    ).limit(limit + 1)
    run_artifacts = list((await db_session.scalars(statement)).all())
    has_more = len(run_artifacts) > limit
    items = run_artifacts[:limit]
    next_database_key = None
    if has_more and items:
        last_run_artifact = items[-1]
        next_database_key = DatabaseQueryPaginationKey(
            created_at=last_run_artifact.created_at,
            identifier=last_run_artifact.artifact_id,
            query_fingerprint=query_fingerprint,
        )
    return RunArtifactDatabasePage(items=items, next_database_key=next_database_key)
