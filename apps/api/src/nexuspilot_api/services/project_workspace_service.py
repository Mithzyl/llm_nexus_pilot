"""Save explicit project copies in object storage; materialize only on an authorized request.

The first version is flat and immutable. It never scans host directories, edits source uploads,
enables Memory, or removes saved objects. A reserved row keeps an interrupted publication visible.
"""

import asyncio
import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from nexuspilot_api.core.config import get_settings
from nexuspilot_api.core.errors import ResourceConflictError, ResourceNotFoundError
from nexuspilot_api.features.identity.resource_access import require_resource_owner
from nexuspilot_api.infrastructure.object_storage import ObjectStorageError
from nexuspilot_api.models import LlmProject, LlmRunArtifact, UserFile, new_id
from nexuspilot_api.models.project_file import ProjectFile
from nexuspilot_api.services.user_file_service import read_verified_bytes


async def owned_project(db, project_id, owner, *, lock=False):
    """Require the active project owner before any storage or local directory access."""
    query = select(LlmProject).where(
        LlmProject.project_id == project_id, LlmProject.owner_user_id == owner
    )
    project = await db.scalar(query.with_for_update() if lock else query)
    if project is None:
        raise ResourceNotFoundError("Project")
    if project.status.value != "active":
        raise ResourceConflictError("Project is not active")
    return project


async def owned_project_file(db, project_id, file_id, owner):
    """Resolve the entry only inside the authenticated project, including reserved copies."""
    await owned_project(db, project_id, owner)
    entry = await db.scalar(
        select(ProjectFile).where(
            ProjectFile.project_id == project_id,
            ProjectFile.project_file_id == file_id,
        )
    )
    if entry is None:
        raise ResourceNotFoundError("Project file")
    return entry


async def save_project_file(db, storage, project_id, owner, payload):
    """Publish one retry-stable independent copy; reserve SQL before writing MinIO bytes.

    The project lock serializes quota/name reservations on MySQL. Failed object publication leaves
    a reserved row for same-key retry; neither the source nor an earlier saved file is overwritten.
    """
    await owned_project(db, project_id, owner, lock=True)
    key = hashlib.sha256(payload.idempotency_key.encode()).hexdigest()
    fingerprint = hashlib.sha256(
        json.dumps(payload.model_dump(exclude={"idempotency_key"}), sort_keys=True).encode()
    ).hexdigest()
    entry = await db.scalar(
        select(ProjectFile).where(ProjectFile.project_id == project_id, ProjectFile.key_hash == key)
    )
    if entry:
        if entry.request_hash != fingerprint:
            raise ResourceConflictError("Save key was used for different input")
        if entry.status == "ready":
            await db.commit()
            return entry
    source_type = "file" if payload.file_id else "artifact"
    source_id = payload.file_id or payload.artifact_id
    model = UserFile if payload.file_id else LlmRunArtifact
    await require_resource_owner(db, model, source_id, owner)
    source = await db.get(model, source_id)
    if source_type == "file" and source.status != "ready":
        raise ResourceConflictError("Source upload is not ready")
    if source.size_bytes > 20 * 1024 * 1024:
        raise HTTPException(413, "项目文件不能超过20 MiB。")
    if entry is None:
        existing_name = await db.scalar(
            select(ProjectFile.project_file_id).where(
                ProjectFile.project_id == project_id,
                ProjectFile.filename == payload.filename,
            )
        )
        if existing_name:
            raise ResourceConflictError("Project filename already exists; choose another name")
        count, total = (
            await db.execute(
                select(func.count(), func.coalesce(func.sum(ProjectFile.size_bytes), 0)).where(
                    ProjectFile.project_id == project_id
                )
            )
        ).one()
        if count >= 500 or total + source.size_bytes > 500 * 1024 * 1024:
            raise ResourceConflictError("Project file quota exceeded")
        identity = new_id()
        entry = ProjectFile(
            project_file_id=identity,
            project_id=project_id,
            filename=payload.filename,
            source_type=source_type,
            source_id=source_id,
            key_hash=key,
            request_hash=fingerprint,
            storage_uri=storage.uri_for_object(f"project-files/{project_id}/{identity}"),
            content_hash=source.content_hash,
            size_bytes=source.size_bytes,
            mime_type=source.mime_type,
            status="reserved",
        )
        db.add(entry)
        try:
            await db.commit()
        except IntegrityError as exc:
            await db.rollback()
            raise ResourceConflictError("Concurrent project save; retry with the same key") from exc
    else:
        await db.commit()
    content = await read_verified_bytes(
        storage, source.storage_uri, entry.size_bytes, entry.content_hash
    )
    try:
        saved = await storage.put_bytes(
            f"project-files/{project_id}/{entry.project_file_id}", content, entry.mime_type
        )
    except ObjectStorageError as exc:
        raise HTTPException(503, "项目文件保存未完成，请使用原保存请求重试。") from exc
    if saved.content_hash != entry.content_hash or saved.size_bytes != entry.size_bytes:
        raise ResourceConflictError("Project copy verification failed")
    entry.status = "ready"
    await db.commit()
    await db.refresh(entry)
    return entry


async def project_file_bytes(storage, entry):
    """Read the saved copy, never silently fall back to the mutable/deleted source."""
    if entry.status != "ready":
        raise ResourceConflictError("Project copy is not ready; retry the original save")
    return await read_verified_bytes(
        storage, entry.storage_uri, entry.size_bytes, entry.content_hash
    )


def materialize_project_copy(root, project_id, file_id, content):
    """Atomically publish a read-only version under generated IDs without following symlinks.

    A configured absolute root must already exist. No user-selected host path is accepted.
    Existing versions must match exactly; local edits never overwrite MinIO or earlier versions.
    """
    path = Path(root)
    if not path.is_absolute():
        raise ValueError("Workspace root must be absolute")
    descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    temporary = None
    try:
        for part in path.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        try:
            os.mkdir(project_id, mode=0o700, dir_fd=descriptor)
        except FileExistsError:
            pass
        child = os.open(project_id, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
        os.close(descriptor)
        descriptor = child
        temporary = f".{uuid4()}.tmp"
        fd = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=descriptor,
        )
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
            os.fchmod(stream.fileno(), 0o400)
        try:
            os.link(
                temporary,
                file_id,
                src_dir_fd=descriptor,
                dst_dir_fd=descriptor,
                follow_symlinks=False,
            )
        except FileExistsError:
            fd = os.open(file_id, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=descriptor)
            with os.fdopen(fd, "rb") as stream:
                if stream.read(len(content) + 1) != content:
                    raise ValueError("Existing workspace copy differs") from None
        os.fsync(descriptor)
    finally:
        try:
            if temporary is not None:
                try:
                    os.unlink(temporary, dir_fd=descriptor)
                except FileNotFoundError:
                    pass  # Creation itself may have failed; no published file is removed.
        finally:
            os.close(descriptor)


async def materialize_saved_file(storage, entry):
    """Export one saved immutable copy only when an operator configured a controlled root."""
    root = get_settings().project_workspace_root
    if not root:
        raise HTTPException(503, "项目本地目录尚未配置，MinIO中的文件仍可下载。")
    content = await project_file_bytes(storage, entry)
    try:
        await asyncio.to_thread(
            materialize_project_copy, root, entry.project_id, entry.project_file_id, content
        )
    except (OSError, ValueError) as exc:
        raise HTTPException(409, "本地副本不可写入或已有内容不同；未覆盖原文件。") from exc
