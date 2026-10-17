"""Resolve trusted Run artifacts into provider-independent immutable Context snapshots.

Private uploads reuse their verified immutable parse objects; legacy trusted artifacts
are parsed on demand. Selected blocks enter Context snapshots; originals stay in MinIO.
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
    select_file_blocks,
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
    """Read scoped sources; private uploads may come from earlier turns of the same conversation."""

    artifact = await db_session.get(LlmRunArtifact, reference.artifact_id)
    if artifact is None:
        raise ResourceNotFoundError("Artifact")
    from nexuspilot_api.models import UserFile
    from nexuspilot_api.services.conversation_file_service import conversation_file_ids

    private_file = await db_session.get(UserFile, artifact.artifact_id, populate_existing=True)
    if private_file is not None and (
        private_file.status != "ready" or private_file.run_id != artifact.run_id
    ):
        raise ResourceConflictError("文件已删除或不可读取。")
    if artifact.run_id != run_id:
        if private_file is None or artifact.artifact_id not in await conversation_file_ids(
            db_session, run_id
        ):
            raise ResourceConflictError("文件不属于当前会话已提交的历史附件。")
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
    """Pin a verified source; default private uploads expose a manifest, never full text.

    Sources without an inventory retain their previous hash and snapshot shape.
    Native selections receive a range-bound source version while retaining the
    complete document's hash, optional inventory, warnings and uncovered-page facts.
    """

    parse_request, content = await _read_parse_source(
        db_session,
        storage,
        run_id=run_id,
        reference=reference,
    )
    try:
        from nexuspilot_api.models import UserFile
        from nexuspilot_api.services.user_file_service import read_parsed_file

        private_file = await db_session.get(UserFile, reference.artifact_id)
        if private_file is not None:
            document = await read_parsed_file(storage, private_file)
        else:
            document = await to_thread.run_sync(
                parse_file_content,
                parse_request,
                content,
                limiter=FILE_PARSE_LIMITER,
            )
        # Preserve old text/PDF/legacy-Office hashes when no structure was supplied.
        absent_fields = {"structure"} if document.structure is None else set()
        document_hash = hashlib.sha256(
            document.model_dump_json(exclude=absent_fields).encode()
        ).hexdigest()
        if (
            private_file is not None
            and reference.selection is None
            and reference.block_limit is None
            and reference.block_offset == 0
        ):
            # Do not include structure inventories: even metadata can be large.
            # Source identity and the complete parse remain pinned and verified;
            # only successful tool reads establish what the model actually saw.
            snapshot = document.model_dump(
                include={
                    "source_id",
                    "source_hash",
                    "filename",
                    "media_type",
                    "parser_name",
                    "parser_version",
                    "extraction_method",
                    "status",
                    "total_pages",
                    "uncovered_pages",
                    "warnings",
                }
            )
            snapshot.update(
                schema_version="file_manifest.v1",
                document_hash=document_hash,
                parsed_hash=private_file.parsed_hash,
                parsed_size_bytes=private_file.parsed_size,
                reading={
                    "offset": 0,
                    "total_blocks": len(document.blocks),
                    "next_offset": 0 if document.blocks else None,
                    "has_more": bool(document.blocks),
                    "returned_blocks": 0,
                },
                read_instruction=(
                    "File text has not been read. Use read_file_content(file_id=source_id, "
                    "offset=0, limit=50), search_file_content or get_file_outline as needed. "
                    "Follow next_offset for subsequent pages; do not claim unread content "
                    "or unsupported graphics/OCR were inspected."
                ),
            )
            return snapshot, document_hash
        selected_document = document
        if reference.selection is not None:
            selected_document = document.model_copy(
                update={"blocks": select_file_blocks(document, reference.selection)}
            )
        if reference.block_limit is None:
            if reference.block_offset:
                raise FileParsingError("A block offset requires an explicit block limit")
            blocks = selected_document.blocks
            reading = {
                "offset": 0,
                "total_blocks": len(selected_document.blocks),
                "next_offset": None,
                "has_more": False,
            }
        else:
            page = read_file_content(
                selected_document,
                offset=reference.block_offset,
                limit=reference.block_limit,
            )
            blocks = page.blocks
            if not blocks:
                raise FileParsingError("Requested Artifact range has no readable content")
            reading = page.model_dump(exclude={"blocks", "header_inferred"})
            reading["offset"] = reference.block_offset
        snapshot = document.model_dump(exclude={"blocks"} | absent_fields)
        snapshot.update(
            document_hash=document_hash,
            blocks=[b.model_dump() for b in blocks],
            reading=reading,
        )
        if reference.selection is not None:
            snapshot["selection"] = reference.selection.model_dump(mode="json")
            reading["document_total_blocks"] = len(document.blocks)
            snapshot["selection_hash"] = _hash_selected_snapshot(snapshot)
    except (FileParsingError, ValueError) as exc:
        raise ResourceConflictError(str(exc)) from exc
    if len(json.dumps(snapshot, ensure_ascii=False).encode()) > MAX_PARSED_CONTEXT_BYTES:
        raise ResourceConflictError(
            "Parsed content exceeds Context storage; select a smaller block range"
        )
    return snapshot, snapshot.get("selection_hash", document_hash)


def _hash_selected_snapshot(snapshot: dict) -> str:
    """Bind a native selection, pagination and exact returned blocks to the full parse hash."""

    serialized = json.dumps(
        {key: value for key, value in snapshot.items() if key != "selection_hash"},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(serialized.encode()).hexdigest()


async def validate_parsed_context_sources(
    db_session: AsyncSession,
    storage: ObjectStorage,
    context: ContextBuildRead,
) -> None:
    """Recheck pinned artifact identity and bytes before generation, including Context replay.

    Historical Context reads remain immutable; dispatch must not bypass missing or
    replaced originals. Manifest snapshots also verify the pinned complete parse,
    without turning that parse into an automatically included model message.
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
                or snapshot.get("selection_hash", snapshot["document_hash"])
                != message.source_version
                or (
                    "selection" in snapshot
                    and snapshot.get("selection_hash") != _hash_selected_snapshot(snapshot)
                )
            ):
                raise ResourceConflictError("Parsed Context source changed after snapshot")
            if snapshot.get("schema_version") == "file_manifest.v1":
                from nexuspilot_api.models import UserFile
                from nexuspilot_api.services.user_file_service import read_parsed_file

                file = await db_session.get(UserFile, message.source_id, populate_existing=True)
                if (
                    file is None
                    or file.parsed_hash != snapshot["parsed_hash"]
                    or file.parsed_size != snapshot["parsed_size_bytes"]
                ):
                    raise ResourceConflictError("Parsed file version changed after snapshot")
                document = await read_parsed_file(storage, file)
                absent = {"structure"} if document.structure is None else set()
                if (
                    hashlib.sha256(document.model_dump_json(exclude=absent).encode()).hexdigest()
                    != snapshot["document_hash"]
                ):
                    raise ResourceConflictError("Parsed file document changed after snapshot")
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ResourceConflictError("Parsed Context snapshot is invalid") from exc
