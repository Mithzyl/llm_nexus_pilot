"""Versioned HTTP routes for phase-one run tracking and artifact persistence."""

from collections.abc import AsyncIterator
from pathlib import PurePath
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from nexuspilot_api.auth import require_api_key
from nexuspilot_api.config import get_settings
from nexuspilot_api.database import get_session
from nexuspilot_api.dependencies import (
    ModelInvocationServiceDependency,
    ProviderRegistryDependency,
)
from nexuspilot_api.models import LlmArtifact, new_id
from nexuspilot_api.response_schemas import ResponsesRequest, ResponsesResult
from nexuspilot_api.schemas import (
    ArtifactRead,
    AttemptCreate,
    AttemptRead,
    RunCreate,
    RunDetail,
    RunRead,
    TaskCreate,
    TaskRead,
    UserCreate,
    UserRead,
)
from nexuspilot_api.service import (
    create_attempt,
    create_run,
    create_task,
    create_user,
    get_run_detail,
    require_run,
    require_task,
)
from nexuspilot_api.storage import ObjectStorage, get_object_storage

router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_api_key)])
Session = Annotated[AsyncSession, Depends(get_session)]


@router.get("/providers")
async def get_providers(registry: ProviderRegistryDependency) -> dict[str, list[str]]:
    """List provider adapters currently registered from server-side configuration."""

    return {"providers": [name.value for name in registry.registered_names]}


@router.post("/responses", response_model=None)
async def post_response(
    payload: ResponsesRequest,
    service: ModelInvocationServiceDependency,
) -> ResponsesResult | StreamingResponse:
    """Create a provider-routed response or return normalized SSE when stream is true."""

    if not payload.stream:
        return await service.generate(payload)

    async def event_source() -> AsyncIterator[str]:
        """Serialize provider-neutral stream events using the SSE wire format."""

        async for event in service.stream(payload):
            data = event.model_dump_json()
            yield f"event: {event.type.value}\ndata: {data}\n\n"

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/users", response_model=UserRead, status_code=status.HTTP_201_CREATED)
async def post_user(payload: UserCreate, session: Session) -> UserRead:
    """Create a basic active platform identity for subsequent run ownership."""

    return UserRead.model_validate(await create_user(session, payload))


@router.post("/runs", response_model=RunRead, status_code=status.HTTP_201_CREATED)
async def post_run(payload: RunCreate, session: Session) -> RunRead:
    """Create one pending run representing a complete user request."""

    return RunRead.model_validate(await create_run(session, payload))


@router.get("/runs/{run_id}", response_model=RunDetail)
async def get_run(run_id: str, session: Session) -> RunDetail:
    """Return the run and its complete phase-one task, attempt, and artifact history."""

    return RunDetail.model_validate(await get_run_detail(session, run_id))


@router.post("/runs/{run_id}/tasks", response_model=TaskRead, status_code=status.HTTP_201_CREATED)
async def post_task(run_id: str, payload: TaskCreate, session: Session) -> TaskRead:
    """Create a task inside a run after validating all dependency edges."""

    return TaskRead.model_validate(await create_task(session, run_id, payload))


@router.get("/tasks/{task_id}", response_model=TaskRead)
async def get_task(task_id: str, session: Session) -> TaskRead:
    """Return the latest persisted status and limits for one task."""

    return TaskRead.model_validate(await require_task(session, task_id))


@router.post(
    "/runs/{run_id}/attempts",
    response_model=AttemptRead,
    status_code=status.HTTP_201_CREATED,
)
async def post_attempt(run_id: str, payload: AttemptCreate, session: Session) -> AttemptRead:
    """Persist an actual model call and update run-level estimated cost."""

    return AttemptRead.model_validate(await create_attempt(session, run_id, payload))


@router.post(
    "/runs/{run_id}/artifacts",
    response_model=ArtifactRead,
    status_code=status.HTTP_201_CREATED,
)
async def post_artifact(
    run_id: str,
    session: Session,
    storage: Annotated[ObjectStorage, Depends(get_object_storage)],
    file: Annotated[UploadFile, File()],
    artifact_type: Annotated[str, Form(min_length=1, max_length=64)],
    task_id: Annotated[str | None, Form()] = None,
) -> ArtifactRead:
    """Upload a bounded artifact to MinIO and persist only its searchable metadata in MySQL."""

    await require_run(session, run_id)
    if task_id:
        task = await require_task(session, task_id)
        if task.run_id != run_id:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Task does not belong to the run",
            )
    filename = PurePath(file.filename or "artifact.bin").name
    max_size = get_settings().max_artifact_size_bytes
    content = await file.read(max_size + 1)
    if len(content) > max_size:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Artifact exceeds {max_size} bytes",
        )
    content_type = file.content_type or "application/octet-stream"
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
        f"{run_id}/{artifact.artifact_id}/{filename}", content, content_type
    )
    artifact.content_hash = stored.content_hash
    artifact.storage_uri = stored.uri
    artifact.size_bytes = stored.size_bytes
    await session.commit()
    await session.refresh(artifact)
    return ArtifactRead.model_validate(artifact)
