"""Resolve trusted Run artifacts into provider-independent immutable Context snapshots.

No upload lifecycle or parse-cache table is introduced. Only selected extraction
blocks are persisted in existing Context sources; complete originals stay in MinIO.
"""

import hashlib
import json

from anyio import CapacityLimiter, to_thread
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from nexuspilot_api.core.errors import ResourceConflictError, ResourceNotFoundError
from nexuspilot_api.infrastructure.object_storage import ObjectStorage, ObjectStorageError
from nexuspilot_api.models import LlmRunArtifact
from nexuspilot_api.schemas.context_builds import ContextBuildRead
from nexuspilot_api.schemas.file_parsing import FileParseRequest, ParsedArtifactReference
from nexuspilot_api.services.file_parsing_service import (
    MAX_PARSE_BYTES,
    FileParsingError,
    parse_file_content,
    read_file_content,
)

PARSED_ARTIFACT_SOURCE = "parsed_artifact"
# Existing MySQL Context content_text is TEXT, not MEDIUMTEXT. Never overflow it.
MAX_PARSED_CONTEXT_BYTES = 60000
FILE_PARSE_LIMITER = CapacityLimiter(2)


async def _read_parse_source(
    db_session: AsyncSession,
    storage: ObjectStorage,
    *,
    run_id: str,
    reference: ParsedArtifactReference,
) -> tuple[FileParseRequest, bytes]:
    """Read one bounded, Run-scoped source and verify its hash without invoking any parser."""

    artifact = await db_session.get(LlmRunArtifact, reference.artifact_id)
    if artifact is None:
        raise ResourceNotFoundError("Artifact")
    if artifact.run_id != run_id:
        raise ResourceConflictError("Parsed Artifact does not belong to the Run")
    if not 0 < artifact.size_bytes <= MAX_PARSE_BYTES:
        raise ResourceConflictError("Artifact exceeds the supported parsing size")
    try:
        parse_request = FileParseRequest(
            source_id=artifact.artifact_id,
            source_hash=artifact.content_hash,
            filename=artifact.filename,
            media_type=artifact.mime_type,
        )
    except ValidationError as exc:
        raise ResourceConflictError("Artifact parsing metadata is invalid") from exc
    try:
        opened = await storage.open_object(
            artifact.storage_uri,
            expected_size_bytes=artifact.size_bytes,
        )

        def collect_content() -> bytes:
            """Validate storage metadata and exact stream size while always closing the iterator."""

            chunks = iter(opened.chunks)
            content = bytearray()
            try:
                for chunk in chunks:
                    # Enter the storage generator before rejecting metadata so its
                    # finally block can release the already-open HTTP response.
                    if (
                        opened.size_bytes != artifact.size_bytes
                        or opened.content_type != artifact.mime_type
                    ):
                        raise ResourceConflictError(
                            "Parsed Artifact storage metadata does not match"
                        )
                    if len(content) + len(chunk) > artifact.size_bytes:
                        raise ResourceConflictError("Artifact stream exceeds declared size")
                    content.extend(chunk)
            finally:
                close = getattr(chunks, "close", None)
                if close is not None:
                    close()
            if (
                len(content) != artifact.size_bytes
                or opened.size_bytes != artifact.size_bytes
                or opened.content_type != artifact.mime_type
            ):
                raise ResourceConflictError("Artifact stream is incomplete")
            return bytes(content)

        content = await to_thread.run_sync(collect_content)
    except ObjectStorageError as exc:
        raise ResourceConflictError("Parsed Artifact object is unavailable or invalid") from exc
    if hashlib.sha256(content).hexdigest() != parse_request.source_hash:
        raise ResourceConflictError("Parsed Artifact content hash does not match source")
    return parse_request, content


async def parse_run_artifact(
    db_session: AsyncSession,
    storage: ObjectStorage,
    *,
    run_id: str,
    reference: ParsedArtifactReference,
) -> tuple[dict, str]:
    """Parse a verified source and select explicit blocks; no bytes or provider routing leak out."""

    parse_request, content = await _read_parse_source(
        db_session,
        storage,
        run_id=run_id,
        reference=reference,
    )
    try:
        document = await to_thread.run_sync(
            parse_file_content,
            parse_request,
            content,
            limiter=FILE_PARSE_LIMITER,
        )
        document_hash = hashlib.sha256(document.model_dump_json().encode()).hexdigest()
        if reference.block_limit is None:
            if reference.block_offset:
                raise FileParsingError("A block offset requires an explicit block limit")
            blocks = document.blocks
            reading = {
                "offset": 0,
                "total_blocks": len(blocks),
                "next_offset": None,
                "has_more": False,
            }
        else:
            page = read_file_content(
                document,
                offset=reference.block_offset,
                limit=reference.block_limit,
            )
            blocks = page.blocks
            if not blocks:
                raise FileParsingError("Requested Artifact range has no readable content")
            reading = page.model_dump(exclude={"blocks", "header_inferred"})
            reading["offset"] = reference.block_offset
        snapshot = document.model_dump(exclude={"blocks"})
        snapshot.update(
            document_hash=document_hash,
            blocks=[b.model_dump() for b in blocks],
            reading=reading,
        )
    except (FileParsingError, ValueError) as exc:
        raise ResourceConflictError(str(exc)) from exc
    if len(json.dumps(snapshot, ensure_ascii=False).encode()) > MAX_PARSED_CONTEXT_BYTES:
        raise ResourceConflictError(
            "Parsed content exceeds Context storage; select a smaller block range"
        )
    return snapshot, document_hash


async def validate_parsed_context_sources(
    db_session: AsyncSession,
    storage: ObjectStorage,
    context: ContextBuildRead,
) -> None:
    """Recheck pinned artifact identity and bytes before generation, including Context replay.

    Historical Context reads remain immutable; dispatch must not bypass missing or
    replaced originals. This trusted API baseline is not final-user file authorization.
    """

    for message in context.messages:
        if message.source_type != PARSED_ARTIFACT_SOURCE:
            continue
        if context.run_id is None:
            raise ResourceConflictError("Parsed Context has no Run anchor")
        try:
            snapshot = json.loads(message.content.split("\n", 1)[1].rsplit("\n", 1)[0])
            request, _ = await _read_parse_source(
                db_session,
                storage,
                run_id=context.run_id,
                reference=ParsedArtifactReference(artifact_id=message.source_id),
            )
            if (
                snapshot["source_id"] != request.source_id
                or snapshot["source_hash"] != request.source_hash
                or snapshot["filename"] != request.filename
                or snapshot["media_type"] != request.media_type.split(";", 1)[0].strip().lower()
                or snapshot["document_hash"] != message.source_version
            ):
                raise ResourceConflictError("Parsed Context source changed after snapshot")
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ResourceConflictError("Parsed Context snapshot is invalid") from exc
