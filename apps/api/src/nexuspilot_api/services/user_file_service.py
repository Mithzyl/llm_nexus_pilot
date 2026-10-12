"""Private immutable uploads, bounded parsing, atomic binding and exact-key cleanup.

Objects are reserved before I/O. Failed writes retain durable keys until cleanup
succeeds; no bucket-wide orphan guesses or automatic model calls are permitted.
"""

import hashlib
import time
from datetime import UTC, datetime, timedelta
from pathlib import PurePosixPath

from anyio import to_thread
from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from nexuspilot_api.core.config import get_settings
from nexuspilot_api.core.errors import ResourceConflictError, ResourceNotFoundError
from nexuspilot_api.infrastructure.file_runtime_client import file_runtime_status
from nexuspilot_api.infrastructure.object_storage import ObjectStorage, ObjectStorageError
from nexuspilot_api.models import LlmRunArtifact, User, UserFile, new_id
from nexuspilot_api.schemas.file_parsing import FileParseRequest, ParsedFileDocument
from nexuspilot_api.schemas.user_files import FileUploadCapabilities, UserFileCreate
from nexuspilot_api.services.file_parsing_service import FileParsingError, parse_file_content

FILE_TYPES = {
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".py": "text/x-python",
    ".js": "text/javascript",
    ".ts": "text/plain",
    ".css": "text/css",
    ".json": "application/json",
    ".csv": "text/csv",
    ".tsv": "text/tab-separated-values",
    ".yaml": "application/yaml",
    ".yml": "application/yaml",
    ".xml": "application/xml",
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}


async def file_upload_capabilities() -> FileUploadCapabilities:
    """Report parser availability off the event loop; existing parsed files remain readable."""
    status = await to_thread.run_sync(file_runtime_status)
    reasons = {
        "not_configured": "服务器尚未启用 Office／PDF 文件解析，可上传文本文件。",
        "unavailable": "Office／PDF 解析服务暂不可用，可上传文本文件或稍后重试。",
        "busy": "Office／PDF 解析服务繁忙，请稍后重试。",
    }
    return FileUploadCapabilities(
        accepted_extensions=[
            extension
            for extension in FILE_TYPES
            if status == "available" or extension not in {".docx", ".xlsx", ".pptx", ".pdf"}
        ],
        executor_status=status,
        unavailable_reason=reasons.get(status),
    )


async def owned_file(db: AsyncSession, file_id: str, user_id: str) -> UserFile:
    """Resolve an owner-filtered record; never disclose another user's file existence."""

    file = await db.scalar(
        select(UserFile)
        .where(
            UserFile.file_id == file_id,
            UserFile.user_id == user_id,
        )
        .execution_options(populate_existing=True)
    )
    if file is None or file.status == "deleted":
        raise ResourceNotFoundError("File")
    return file


async def list_private_files(db: AsyncSession, user_id: str) -> list[UserFile]:
    """Return the bounded live inventory including incomplete records requiring cleanup."""
    return list(
        (
            await db.scalars(
                select(UserFile)
                .where(
                    UserFile.user_id == user_id,
                    UserFile.status != "deleted",
                )
                .order_by(UserFile.created_at.desc())
                .limit(500)
            )
        ).all()
    )


async def begin_upload(db: AsyncSession, file_id: str, user_id: str) -> UserFile:
    """Claim a nonexpired reservation exactly once before reading potentially slow request bytes."""
    file = await owned_file(db, file_id, user_id)
    if file.status == "ready":
        return file
    result = await db.execute(
        update(UserFile)
        .where(
            UserFile.file_id == file_id,
            UserFile.status == "reserved",
            UserFile.expires_at > datetime.now(UTC),
        )
        .values(status="uploading")
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        raise ResourceConflictError("上传正在执行、已过期或已失败，请检查文件状态。")
    await db.commit()
    await db.refresh(file)
    return file


async def interrupt_upload(db: AsyncSession, file_id: str) -> None:
    """Record interrupted reads without overwriting a ready result or deletion decision."""
    await db.rollback()
    await db.execute(
        update(UserFile)
        .where(
            UserFile.file_id == file_id,
            UserFile.status == "uploading",
        )
        .values(status="failed", error="上传中断或解析失败，请删除后重新上传。")
    )
    await db.commit()


async def reserve_file(
    db: AsyncSession, storage: ObjectStorage, user_id: str, payload: UserFileCreate
) -> UserFile:
    """Replay identical uploads; reject unavailable parsers before reserving storage quota."""

    filename = payload.filename
    if (
        filename != PurePosixPath(filename).name
        or "\\" in filename
        or any(ord(c) < 32 or ord(c) == 127 for c in filename)
    ):
        raise HTTPException(422, "文件名不能包含路径或控制字符。")
    mime_type = FILE_TYPES.get(PurePosixPath(filename).suffix.lower())
    if mime_type is None:
        raise HTTPException(422, "暂不支持此文件格式；图片和扫描件尚不支持 OCR。")
    await db.scalar(select(User).where(User.user_id == user_id).with_for_update())
    key_hash = hashlib.sha256(payload.idempotency_key.encode()).hexdigest()
    previous = await db.scalar(
        select(UserFile)
        .where(
            UserFile.user_id == user_id,
            UserFile.key_hash == key_hash,
        )
        .with_for_update()
    )
    if previous:
        if (previous.filename, previous.size_bytes, previous.content_hash) != (
            filename,
            payload.size_bytes,
            payload.content_hash,
        ):
            raise ResourceConflictError("Upload key was used for different content")
        await db.commit()
        return previous
    if PurePosixPath(filename).suffix.lower() in {".docx", ".xlsx", ".pptx", ".pdf"}:
        capabilities = await file_upload_capabilities()
        if capabilities.executor_status != "available":
            raise HTTPException(503, capabilities.unavailable_reason)
    # Locking reads use current committed state even after authentication opened a
    # MySQL repeatable-read snapshot. Reserve parser-result space before any I/O.
    sizes = list(
        (
            await db.scalars(
                select(UserFile.size_bytes)
                .where(
                    UserFile.user_id == user_id,
                    UserFile.status != "deleted",
                )
                .with_for_update()
            )
        ).all()
    )
    used, count = sum(size + 55000 for size in sizes), len(sizes)
    settings = get_settings()
    if (
        used + payload.size_bytes + 55000 > settings.user_file_quota_bytes
        or count >= settings.user_file_count_limit
    ):
        raise HTTPException(413, "文件存储额度不足，请删除不再需要的文件。")
    file_id = new_id()
    file = UserFile(
        file_id=file_id,
        user_id=user_id,
        key_hash=key_hash,
        filename=filename,
        mime_type=mime_type,
        size_bytes=payload.size_bytes,
        content_hash=payload.content_hash,
        storage_uri=storage.uri_for_object(f"user-files/{file_id}/source"),
        parsed_uri=storage.uri_for_object(f"user-files/{file_id}/parsed"),
        status="reserved",
        expires_at=datetime.now(UTC) + timedelta(hours=2),
    )
    db.add(file)
    await db.commit()
    await db.refresh(file)
    return file


async def read_verified_bytes(storage: ObjectStorage, uri: str, size: int, digest: str) -> bytes:
    """Read a bounded object off the event loop, verify its digest and always close its stream."""

    opened = await storage.open_object(uri, expected_size_bytes=size)

    def collect():
        """Consume at most the declared bytes and release the MinIO connection on failure."""

        chunks = iter(opened.chunks)
        result = bytearray()
        try:
            for chunk in chunks:
                result.extend(chunk)
                if len(result) > size:
                    raise ObjectStorageError("Stored file size mismatch")
        finally:
            close = getattr(chunks, "close", None)
            if close:
                close()
        if len(result) != size or hashlib.sha256(result).hexdigest() != digest:
            raise ObjectStorageError("Stored file integrity mismatch")
        return bytes(result)

    return await to_thread.run_sync(collect)


async def finish_upload(
    db: AsyncSession, storage: ObjectStorage, file: UserFile, content: bytes
) -> UserFile:
    """Parse and verify both persisted objects before publishing; retain failure cleanup state."""

    from nexuspilot_api.services.parsed_artifact_service import FILE_PARSE_LIMITER

    file_id, storage_uri, parsed_uri = file.file_id, file.storage_uri, file.parsed_uri
    started = time.perf_counter()
    try:
        if (
            len(content) != file.size_bytes
            or hashlib.sha256(content).hexdigest() != file.content_hash
        ):
            raise FileParsingError("文件大小或校验值不一致，请重新选择文件。")
        document = await to_thread.run_sync(
            parse_file_content,
            FileParseRequest(
                source_id=file.file_id,
                source_hash=file.content_hash,
                filename=file.filename,
                media_type=file.mime_type,
            ),
            content,
            limiter=FILE_PARSE_LIMITER,
        )
        parsed = document.model_dump_json().encode()
        # Context currently uses MySQL TEXT; reject before saving a turn rather than truncate.
        if len(parsed) > 55000:
            raise FileParsingError("提取内容过大，请拆分文件后上传（当前尚无分段选择入口）。")
        await storage.put_bytes(f"user-files/{file.file_id}/source", content, file.mime_type)
        parsed_object = await storage.put_bytes(
            f"user-files/{file.file_id}/parsed", parsed, "application/json"
        )
        await read_verified_bytes(storage, file.storage_uri, file.size_bytes, file.content_hash)
        await read_verified_bytes(storage, file.parsed_uri, len(parsed), parsed_object.content_hash)
        result = await db.execute(
            update(UserFile)
            .where(
                UserFile.file_id == file_id,
                UserFile.status == "uploading",
            )
            .values(
                status="ready",
                parsed_hash=parsed_object.content_hash,
                parsed_size=len(parsed),
                error=None,
                parse_summary={
                    "status": document.status,
                    "warnings": document.warnings,
                    "block_count": len(document.blocks),
                    "elapsed_ms": round((time.perf_counter() - started) * 1000),
                },
            )
        )
        if result.rowcount != 1:
            raise ResourceConflictError("Upload no longer active")
        await db.commit()
        await db.refresh(file)
        return file
    except Exception as exc:
        await db.rollback()
        failed_transition = await db.execute(
            update(UserFile)
            .where(
                UserFile.file_id == file_id,
                UserFile.status == "uploading",
            )
            .values(status="failed", error="上传或解析失败，请删除后重试。")
        )
        await db.commit()
        # Exact object keys remain in the failed record if either deletion is unavailable.
        if failed_transition.rowcount == 1:
            try:
                await storage.delete_object(storage_uri)
                await storage.delete_object(parsed_uri)
            except ObjectStorageError:
                pass
        if isinstance(exc, FileParsingError):
            raise HTTPException(422, str(exc)) from exc
        if isinstance(exc, ObjectStorageError):
            raise HTTPException(503, "文件存储暂不可用，请稍后重试。") from exc
        raise


async def delete_file(
    db: AsyncSession, storage: ObjectStorage, file: UserFile, *, cleanup_only: bool = False
) -> bool:
    """Revoke access before deletion; keep deleting state and quota on failed cleanup."""

    now = datetime.now(UTC)
    eligible = (
        UserFile.status.in_(["failed", "deleting"])
        | (UserFile.status.in_(["reserved", "uploading"]) & (UserFile.expires_at <= now))
        if cleanup_only
        else UserFile.status != "deleted"
    )
    result = await db.execute(
        update(UserFile)
        .where(
            UserFile.file_id == file.file_id,
            eligible,
            (UserFile.status != "uploading") | (UserFile.expires_at <= now),
        )
        .values(status="deleting")
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        if cleanup_only:
            await db.rollback()
            return False
        raise ResourceConflictError("文件正在上传或解析，请稍后再删除。")
    await db.commit()

    try:
        await storage.delete_object(file.storage_uri)
        await storage.delete_object(file.parsed_uri)
    except ObjectStorageError as exc:
        raise HTTPException(503, "文件已停止使用，存储清理未完成，请重试删除。") from exc
    await db.execute(
        update(UserFile)
        .where(UserFile.file_id == file.file_id)
        .values(status="deleted", error=None)
    )
    await db.commit()
    return True


async def bind_files(
    db: AsyncSession, user_id: str, run_id: str, file_ids: list[str]
) -> list[dict]:
    """Bind ready immutable files once, in the same transaction as their Run and User Message."""

    if len(file_ids) != len(set(file_ids)):
        raise ResourceConflictError("Duplicate file references")
    metadata = []
    for file_id in sorted(file_ids):
        file = await db.scalar(
            select(UserFile)
            .where(
                UserFile.file_id == file_id,
                UserFile.user_id == user_id,
            )
            .with_for_update()
        )
        if file is None:
            raise ResourceNotFoundError("File")
        if file.status != "ready" or file.run_id is not None:
            raise ResourceConflictError("文件未就绪或已用于其他消息，请重新上传。")
        file.run_id = run_id
        db.add(
            LlmRunArtifact(
                artifact_id=file_id,
                run_id=run_id,
                artifact_type="user-file",
                filename=file.filename,
                mime_type=file.mime_type,
                size_bytes=file.size_bytes,
                content_hash=file.content_hash,
                storage_uri=file.storage_uri,
            )
        )
        metadata.append(
            {"file_id": file_id, "filename": file.filename, "size_bytes": file.size_bytes}
        )
    return metadata


async def read_parsed_file(storage: ObjectStorage, file: UserFile) -> ParsedFileDocument:
    """Reuse the immutable verified parse; revoked files never reach subsequent model calls."""

    if file.status != "ready" or not file.parsed_hash or not file.parsed_size:
        raise ResourceConflictError("文件已删除或不可读取。")
    try:
        content = await read_verified_bytes(
            storage, file.parsed_uri, file.parsed_size, file.parsed_hash
        )
    except ObjectStorageError as exc:
        raise ResourceConflictError("解析结果暂不可读取，请重新上传或稍后重试。") from exc
    document = ParsedFileDocument.model_validate_json(content)
    if document.source_id != file.file_id or document.source_hash != file.content_hash:
        raise ResourceConflictError("Parsed file source mismatch")
    return document
