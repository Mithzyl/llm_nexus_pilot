"""Knowledge document, version, chunking, and bounded retrieval use cases."""

import hashlib
import re

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from nexuspilot_api.core.errors import (
    InvalidRequestError,
    ResourceConflictError,
    ResourceNotFoundError,
)
from nexuspilot_api.features.memory.services.memory_policy import tokenize_memory_text
from nexuspilot_api.infrastructure.object_storage import ObjectStorage
from nexuspilot_api.models import (
    KnowledgeDocumentStatus,
    KnowledgeVersionStatus,
    LlmKnowledgeChunk,
    LlmKnowledgeDocument,
    LlmKnowledgeDocumentVersion,
    LlmRun,
    LlmRunArtifact,
    LlmSession,
    LlmTask,
    User,
    new_id,
)
from nexuspilot_api.schemas.knowledge import (
    KnowledgeDocumentCreate,
    KnowledgeDocumentRead,
    KnowledgeDocumentUpdate,
    KnowledgeRetrievalCreate,
    KnowledgeRetrievalRead,
    KnowledgeRetrievalResultRead,
    KnowledgeVersionCreate,
    KnowledgeVersionRead,
)
from nexuspilot_api.schemas.pagination import CursorPage

MAX_KNOWLEDGE_CHUNK_CHARACTERS = 2_000
MAX_KNOWLEDGE_CANDIDATE_CHUNKS = 500
_WHITESPACE_PATTERN = re.compile(r"\s+")


async def create_knowledge_document(
    db_session: AsyncSession,
    payload: KnowledgeDocumentCreate,
) -> KnowledgeDocumentRead:
    """Create one Knowledge document identity after validating its owner scope."""

    await _validate_owner_scope(
        db_session,
        payload.user_id,
        payload.session_id,
        payload.run_id,
        payload.task_id,
    )
    document = LlmKnowledgeDocument(
        knowledge_document_id=new_id(),
        user_id=payload.user_id,
        session_id=payload.session_id,
        run_id=payload.run_id,
        task_id=payload.task_id,
        document_key=payload.document_key,
        title=payload.title,
        status=KnowledgeDocumentStatus.PENDING,
        current_version_number=0,
    )
    db_session.add(document)
    await db_session.commit()
    await db_session.refresh(document)
    return KnowledgeDocumentRead.model_validate(document)


async def get_knowledge_document(
    db_session: AsyncSession,
    document_id: str,
) -> KnowledgeDocumentRead:
    """Return one Knowledge document or raise the stable not-found error."""

    document = await db_session.get(LlmKnowledgeDocument, document_id)
    if document is None:
        raise ResourceNotFoundError("Knowledge Document")
    return KnowledgeDocumentRead.model_validate(document)


async def add_knowledge_version(
    db_session: AsyncSession,
    storage: ObjectStorage,
    document_id: str,
    payload: KnowledgeVersionCreate,
) -> KnowledgeVersionRead:
    """Extract, chunk, and atomically switch one Knowledge version behind Artifact bytes."""

    document = await db_session.scalar(
        select(LlmKnowledgeDocument)
        .where(LlmKnowledgeDocument.knowledge_document_id == document_id)
        .with_for_update()
    )
    if document is None:
        raise ResourceNotFoundError("Knowledge Document")
    artifact = await db_session.get(LlmRunArtifact, payload.artifact_id)
    if artifact is None:
        raise ResourceNotFoundError("Artifact")
    artifact_run = await db_session.get(LlmRun, artifact.run_id)
    if artifact_run is None or artifact_run.user_id != document.user_id:
        raise ResourceConflictError("Artifact does not belong to the Knowledge owner")
    content = await _read_artifact_text(storage, artifact)
    content_hash = hashlib.sha256(content.encode()).hexdigest()
    duplicate = await db_session.scalar(
        select(LlmKnowledgeDocumentVersion).where(
            LlmKnowledgeDocumentVersion.knowledge_document_id == document_id,
            LlmKnowledgeDocumentVersion.content_hash == content_hash,
            LlmKnowledgeDocumentVersion.parser_version == payload.parser_version,
            LlmKnowledgeDocumentVersion.status == KnowledgeVersionStatus.READY,
        )
    )
    if duplicate is not None:
        return KnowledgeVersionRead.model_validate(duplicate)
    version_number = document.current_version_number + 1
    version_id = new_id()
    try:
        chunks = _chunk_text(content)
        version = LlmKnowledgeDocumentVersion(
            knowledge_document_version_id=version_id,
            knowledge_document_id=document_id,
            version_number=version_number,
            artifact_id=artifact.artifact_id,
            artifact_uri=artifact.storage_uri,
            content_hash=content_hash,
            parser_version=payload.parser_version,
            status=KnowledgeVersionStatus.READY,
            chunk_count=len(chunks),
            activated_at=None,
        )
        db_session.add(version)
        await db_session.flush()
        db_session.add_all(
            [
                LlmKnowledgeChunk(
                    knowledge_chunk_id=new_id(),
                    knowledge_document_version_id=version_id,
                    chunk_order=order,
                    content_text=chunk,
                    stable_locator=f"#chunk-{order}",
                    artifact_uri=artifact.storage_uri,
                    content_hash=hashlib.sha256(chunk.encode()).hexdigest(),
                    token_estimate=max(1, len(chunk.encode())),
                )
                for order, chunk in enumerate(chunks, start=1)
            ]
        )
        previous_version = None
        if document.current_version_number > 0:
            previous_version = await db_session.scalar(
                select(LlmKnowledgeDocumentVersion).where(
                    LlmKnowledgeDocumentVersion.knowledge_document_id == document_id,
                    LlmKnowledgeDocumentVersion.version_number
                    == document.current_version_number,
                )
            )
            if previous_version is not None:
                previous_version.status = KnowledgeVersionStatus.INACTIVE
        document.current_version_number = version_number
        document.status = KnowledgeDocumentStatus.READY
        version.activated_at = version.created_at
        await db_session.commit()
    except Exception as exc:
        await db_session.rollback()
        failed_version = LlmKnowledgeDocumentVersion(
            knowledge_document_version_id=version_id,
            knowledge_document_id=document_id,
            version_number=version_number,
            artifact_id=artifact.artifact_id,
            artifact_uri=artifact.storage_uri,
            content_hash=content_hash,
            parser_version=payload.parser_version,
            status=KnowledgeVersionStatus.FAILED,
            chunk_count=0,
            failed_at=None,
            error_code=_safe_error_code(exc),
        )
        db_session.add(failed_version)
        await db_session.commit()
        raise ResourceConflictError("Knowledge version processing failed") from exc
    return KnowledgeVersionRead.model_validate(version)


async def deactivate_knowledge_document(
    db_session: AsyncSession,
    document_id: str,
    payload: KnowledgeDocumentUpdate,
) -> KnowledgeDocumentRead:
    """Deactivate one document and its current version without deleting evidence."""

    document = await db_session.scalar(
        select(LlmKnowledgeDocument)
        .where(LlmKnowledgeDocument.knowledge_document_id == document_id)
        .with_for_update()
    )
    if document is None:
        raise ResourceNotFoundError("Knowledge Document")
    if not payload.deactivate:
        raise InvalidRequestError("Only deactivation is supported for documents")
    if document.status == KnowledgeDocumentStatus.READY:
        current_version = await db_session.scalar(
            select(LlmKnowledgeDocumentVersion).where(
                LlmKnowledgeDocumentVersion.knowledge_document_id == document_id,
                LlmKnowledgeDocumentVersion.version_number
                == document.current_version_number,
            )
        )
        if current_version is not None:
            current_version.status = KnowledgeVersionStatus.INACTIVE
    document.status = KnowledgeDocumentStatus.INACTIVE
    await db_session.commit()
    await db_session.refresh(document)
    return KnowledgeDocumentRead.model_validate(document)


async def retrieve_knowledge(
    db_session: AsyncSession,
    payload: KnowledgeRetrievalCreate,
) -> KnowledgeRetrievalRead:
    """Return a bounded ranked chunk list restricted to ready versions in owner scope."""

    await _validate_owner_scope(
        db_session, payload.user_id, payload.session_id, payload.run_id, payload.task_id
    )
    statement = (
        select(LlmKnowledgeChunk)
        .join(
            LlmKnowledgeDocumentVersion,
            LlmKnowledgeDocumentVersion.knowledge_document_version_id
            == LlmKnowledgeChunk.knowledge_document_version_id,
        )
        .join(
            LlmKnowledgeDocument,
            LlmKnowledgeDocument.knowledge_document_id
            == LlmKnowledgeDocumentVersion.knowledge_document_id,
        )
        .where(
            LlmKnowledgeDocument.user_id == payload.user_id,
            LlmKnowledgeDocument.status == KnowledgeDocumentStatus.READY,
            LlmKnowledgeDocumentVersion.status == KnowledgeVersionStatus.READY,
        )
    )
    if payload.session_id is not None:
        statement = statement.where(LlmKnowledgeDocument.session_id == payload.session_id)
    if payload.run_id is not None:
        statement = statement.where(LlmKnowledgeDocument.run_id == payload.run_id)
    if payload.task_id is not None:
        statement = statement.where(LlmKnowledgeDocument.task_id == payload.task_id)
    if payload.document_id is not None:
        statement = statement.where(
            LlmKnowledgeDocument.knowledge_document_id == payload.document_id
        )
    chunks = list(
        (
            await db_session.scalars(
                statement.order_by(
                    LlmKnowledgeChunk.created_at, LlmKnowledgeChunk.knowledge_chunk_id
                ).limit(MAX_KNOWLEDGE_CANDIDATE_CHUNKS)
            )
        ).all()
    )
    query_terms = set(tokenize_memory_text(payload.query_text))
    ranked: list[tuple[int, LlmKnowledgeChunk]] = []
    for chunk in chunks:
        chunk_terms = set(tokenize_memory_text(chunk.content_text))
        overlap = len(query_terms & chunk_terms)
        if overlap == 0:
            continue
        union = len(query_terms | chunk_terms)
        score = overlap * 10_000 // union if union else 0
        ranked.append((score, chunk))
    ranked.sort(key=lambda item: (-item[0], item[1].knowledge_chunk_id))
    results: list[KnowledgeRetrievalResultRead] = []
    chunk_documents: dict[str, tuple[LlmKnowledgeDocument, LlmKnowledgeDocumentVersion]] = {}
    for _score, chunk in ranked[: payload.limit]:
        entry = chunk_documents.get(chunk.knowledge_document_version_id)
        if entry is None:
            version_row = await db_session.get(
                LlmKnowledgeDocumentVersion, chunk.knowledge_document_version_id
            )
            document_row = (
                await db_session.get(LlmKnowledgeDocument, version_row.knowledge_document_id)
                if version_row
                else None
            )
            entry = (document_row, version_row)
            chunk_documents[chunk.knowledge_document_version_id] = entry
        document_row, version_row = entry
        if document_row is None or version_row is None:
            continue
        results.append(
            KnowledgeRetrievalResultRead(
                rank=len(results) + 1,
                knowledge_document_id=document_row.knowledge_document_id,
                document_key=document_row.document_key,
                version_number=version_row.version_number,
                knowledge_chunk_id=chunk.knowledge_chunk_id,
                chunk_order=chunk.chunk_order,
                stable_locator=chunk.stable_locator,
                content_preview=chunk.content_text[:200],
                content_hash=chunk.content_hash,
                lexical_score=_score,
            )
        )
        if len(results) >= payload.limit:
            break
    return KnowledgeRetrievalRead(
        user_id=payload.user_id,
        query_text=payload.query_text,
        document_id=payload.document_id,
        limit=payload.limit,
        results=results,
    )


async def _read_artifact_text(storage: ObjectStorage, artifact: LlmRunArtifact) -> str:
    """Read and decode one Artifact object into bounded normalized text."""

    content = await storage.open_object(artifact.storage_uri)
    raw = b"".join(content.chunks)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("utf-8", errors="replace")
    return _WHITESPACE_PATTERN.sub(" ", text).strip()


def _chunk_text(text: str) -> list[str]:
    """Split normalized text into bounded chunks without splitting inside words."""

    if not text:
        raise InvalidRequestError("Artifact content is empty")
    chunks: list[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= MAX_KNOWLEDGE_CHUNK_CHARACTERS:
            chunks.append(remaining)
            break
        split_at = remaining.rfind(" ", 0, MAX_KNOWLEDGE_CHUNK_CHARACTERS)
        if split_at <= 0:
            split_at = MAX_KNOWLEDGE_CHUNK_CHARACTERS
        chunks.append(remaining[:split_at])
        remaining = remaining[split_at:].lstrip()
    return chunks


def _safe_error_code(error: Exception) -> str:
    """Classify a chunking failure without copying sensitive text into errors."""

    if isinstance(error, InvalidRequestError):
        return "invalid_content"
    return "processing_failed"


async def _validate_owner_scope(
    db_session: AsyncSession,
    user_id: str,
    session_id: str | None,
    run_id: str | None,
    task_id: str | None,
) -> None:
    """Validate the Knowledge owner chain exactly like Memory scope rules."""

    user = await db_session.get(User, user_id)
    if user is None:
        raise ResourceNotFoundError("User")
    if session_id is not None:
        session = await db_session.get(LlmSession, session_id)
        if session is None:
            raise ResourceNotFoundError("Session")
        if session.user_id != user_id:
            raise ResourceConflictError("Session does not belong to the user")
    if run_id is not None:
        run = await db_session.get(LlmRun, run_id)
        if run is None:
            raise ResourceNotFoundError("Run")
        if run.user_id != user_id:
            raise ResourceConflictError("Run does not belong to the user")
    if task_id is not None:
        task = await db_session.get(LlmTask, task_id)
        if task is None:
            raise ResourceNotFoundError("Task")
        task_run = await db_session.get(LlmRun, task.run_id)
        if task_run is None or task_run.user_id != user_id:
            raise ResourceConflictError("Task does not belong to the user")
        if run_id is not None and task.run_id != run_id:
            raise ResourceConflictError("Task does not belong to the run")


async def list_knowledge_documents(
    db_session: AsyncSession,
    *,
    user_id: str,
    codec,
    cursor: str | None,
    limit: int,
) -> CursorPage[KnowledgeDocumentRead]:
    """Return a filter-bound page of Knowledge documents for one owner."""

    from datetime import UTC

    from sqlalchemy import or_

    from nexuspilot_api.core.errors import InvalidCursorError, ResourceNotFoundError
    from nexuspilot_api.core.pagination import (
        DatabaseQueryPaginationKey,
        database_query_fingerprint,
    )
    from nexuspilot_api.models import User

    user = await db_session.get(User, user_id)
    if user is None:
        raise ResourceNotFoundError("User")
    query_fingerprint = database_query_fingerprint(
        "knowledge-documents", {"user_id": user_id}
    )
    after_database_key = codec.decode_query(cursor) if cursor else None
    if after_database_key and after_database_key.query_fingerprint != query_fingerprint:
        raise InvalidCursorError
    statement = select(LlmKnowledgeDocument).where(
        LlmKnowledgeDocument.user_id == user_id
    )
    if after_database_key is not None:
        cursor_time = after_database_key.created_at.astimezone(UTC).replace(tzinfo=None)
        statement = statement.where(
            or_(
                LlmKnowledgeDocument.created_at > cursor_time,
                and_(
                    LlmKnowledgeDocument.created_at == cursor_time,
                    LlmKnowledgeDocument.knowledge_document_id
                    > after_database_key.identifier,
                ),
            )
        )
    documents = list(
        (
            await db_session.scalars(
                statement.order_by(
                    LlmKnowledgeDocument.created_at,
                    LlmKnowledgeDocument.knowledge_document_id,
                ).limit(limit + 1)
            )
        ).all()
    )
    has_more = len(documents) > limit
    items = documents[:limit]
    next_cursor = None
    if has_more and items:
        last_document = items[-1]
        next_cursor = codec.encode_query(
            DatabaseQueryPaginationKey(
                created_at=last_document.created_at,
                identifier=last_document.knowledge_document_id,
                query_fingerprint=query_fingerprint,
            )
        )
    return CursorPage[KnowledgeDocumentRead](
        items=[KnowledgeDocumentRead.model_validate(item) for item in items],
        next_cursor=next_cursor,
        has_more=next_cursor is not None,
        limit=limit,
    )
