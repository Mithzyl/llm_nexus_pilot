"""Transactional Memory Store, version, source, lifecycle, and deletion use cases."""

import hashlib
from dataclasses import dataclass
from datetime import UTC

from sqlalchemy import and_, delete, or_, select, update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from nexuspilot_api.core.errors import (
    InvalidCursorError,
    InvalidRequestError,
    ResourceConflictError,
    ResourceNotFoundError,
)
from nexuspilot_api.core.pagination import (
    CursorCodec,
    DatabaseQueryPaginationKey,
    DatabaseSequencePaginationKey,
    database_query_fingerprint,
)
from nexuspilot_api.features.memory.schemas.memories import (
    MemoryCreate,
    MemoryMutationRead,
    MemoryRead,
    MemorySourceCreate,
    MemorySourceRead,
    MemorySummary,
    MemoryUpdate,
    MemoryVersionRead,
)
from nexuspilot_api.features.memory.services.credential_guard import (
    reject_sensitive_memory_content,
)
from nexuspilot_api.features.memory.services.memory_policy import (
    MEMORY_TOKEN_ESTIMATOR_VERSION,
    build_memory_search_term_counts,
    estimate_memory_tokens,
    hash_active_semantic_key,
    hash_memory_content,
    hash_memory_request,
    source_trust_level,
    validate_memory_status_transition,
    validate_memory_type_scope,
)
from nexuspilot_api.models import (
    ApprovalMethod,
    LlmMemory,
    LlmMemoryMutation,
    LlmMemorySearchTerm,
    LlmMemorySource,
    LlmMemoryVersion,
    LlmMessage,
    LlmModelAttempt,
    LlmModelToolCall,
    LlmProject,
    LlmRun,
    LlmRunArtifact,
    LlmSession,
    LlmTask,
    MemoryCreatedByType,
    MemoryMutationActorType,
    MemoryMutationOperation,
    MemorySourceType,
    MemoryStatus,
    MemoryTrustLevel,
    MemoryType,
    User,
    new_id,
)
from nexuspilot_api.models.base import utc_now
from nexuspilot_api.schemas.pagination import CursorPage


@dataclass(frozen=True)
class MemoryWriteResult:
    """Return one Memory representation and whether an idempotent request replayed."""

    memory: MemoryRead
    was_replayed: bool


@dataclass(frozen=True)
class ResolvedMemoryScope:
    """Contain a validated owner and the effective nested retrieval scope."""

    user_id: str
    session_id: str | None
    run_id: str | None
    task_id: str | None
    project_id: str | None


@dataclass(frozen=True)
class ValidatedMemorySource:
    """Contain one source locator after ownership and evidence validation."""

    source_type: MemorySourceType
    source_resource_id: str
    source_content_hash: str
    session_id: str | None
    run_id: str | None
    task_id: str | None
    trust_level: MemoryTrustLevel = MemoryTrustLevel.INTERNAL_SYSTEM_RESULT


@dataclass(frozen=True)
class MemoryDatabasePage:
    """Contain one Memory query page and its next query-bound database key."""

    items: list[tuple[LlmMemory, LlmMemoryVersion]]
    next_database_key: DatabaseQueryPaginationKey | None


async def create_memory(
    db_session: AsyncSession,
    payload: MemoryCreate,
) -> MemoryWriteResult:
    """Create one sourced Memory and immutable first version in a single transaction."""

    request_hash = hash_memory_request(payload.model_dump(mode="json"))
    existing = await db_session.scalar(
        select(LlmMemory).where(
            LlmMemory.user_id == payload.user_id,
            LlmMemory.creation_idempotency_key == payload.idempotency_key,
        )
    )
    if existing is not None:
        return await _resolve_creation_replay(db_session, existing, request_hash)

    resolved_scope = await validate_and_resolve_memory_scope(
        db_session,
        user_id=payload.user_id,
        session_id=payload.session_id,
        run_id=payload.run_id,
        task_id=payload.task_id,
        project_id=payload.project_id,
    )
    reject_sensitive_memory_content(payload.content_text)
    validate_memory_type_scope(
        memory_type=payload.memory_type,
        session_id=resolved_scope.session_id,
        run_id=resolved_scope.run_id,
        task_id=resolved_scope.task_id,
        expires_at=payload.expires_at,
        project_id=resolved_scope.project_id,
    )
    validated_sources = await validate_memory_sources(
        db_session,
        scope=resolved_scope,
        sources=payload.sources,
    )
    if payload.created_by_attempt_id is not None:
        await _validate_attempt_owner(
            db_session,
            attempt_id=payload.created_by_attempt_id,
            user_id=payload.user_id,
        )
        if not any(
            source.source_type == MemorySourceType.MODEL_ATTEMPT
            and source.source_resource_id == payload.created_by_attempt_id
            for source in validated_sources
        ):
            raise InvalidRequestError("Model-created Memory requires its model attempt as a source")

    memory_id = new_id()
    memory_version_id = new_id()
    active_semantic_hash = None
    approved_at = None
    if payload.status == MemoryStatus.ACTIVE:
        active_semantic_hash = hash_active_semantic_key(
            user_id=payload.user_id,
            session_id=resolved_scope.session_id,
            run_id=resolved_scope.run_id,
            task_id=resolved_scope.task_id,
            project_id=resolved_scope.project_id,
            memory_type=payload.memory_type,
            semantic_key=payload.semantic_key,
        )
        approved_at = utc_now()
        await _supersede_replaced_memory(
            db_session,
            replacement_memory_id=memory_id,
            replaced_memory_id=payload.supersedes_memory_id,
            expected_active_semantic_hash=active_semantic_hash,
            user_id=payload.user_id,
        )

    memory = LlmMemory(
        memory_id=memory_id,
        user_id=payload.user_id,
        session_id=resolved_scope.session_id,
        run_id=resolved_scope.run_id,
        task_id=resolved_scope.task_id,
        project_id=resolved_scope.project_id,
        memory_type=payload.memory_type,
        status=payload.status,
        current_version_number=1,
        semantic_key=payload.semantic_key,
        active_semantic_key_hash=active_semantic_hash,
        supersedes_memory_id=payload.supersedes_memory_id,
        approval_method=(
            ApprovalMethod.TRUSTED_CALLER if approved_at is not None else ApprovalMethod.NONE
        ),
        approved_at=approved_at,
        sensitivity_classification=payload.sensitivity_classification,
        is_core_profile_eligible=payload.is_core_profile_eligible,
        expires_at=payload.expires_at,
        creation_idempotency_key=payload.idempotency_key,
        creation_request_hash=request_hash,
    )
    memory_version = _create_memory_version(
        memory_version_id=memory_version_id,
        memory_id=memory_id,
        version_number=1,
        content_text=payload.content_text,
        importance=payload.importance,
        confidence=payload.confidence,
        created_by_type=payload.created_by_type,
        created_by_attempt_id=payload.created_by_attempt_id,
    )
    db_session.add(memory)
    db_session.add(memory_version)
    db_session.add(
        LlmMemoryMutation(
            memory_id=memory_id,
            idempotency_key=payload.idempotency_key,
            request_hash=request_hash,
            operation=MemoryMutationOperation.CREATE,
            actor_type=(
                MemoryMutationActorType.MODEL_ATTEMPT
                if payload.created_by_type == MemoryCreatedByType.MODEL_ATTEMPT
                else MemoryMutationActorType.TRUSTED_CALLER
            ),
            actor_id=payload.created_by_attempt_id,
            before_version_number=None,
            after_version_number=1,
            before_status=None,
            after_status=payload.status,
            change_json=None,
            result_version_number=1,
            result_status=payload.status,
        )
    )
    try:
        # Persist parent rows before staging ID-only source and term children.
        await db_session.flush()
        _add_memory_source_and_term_rows(
            db_session,
            memory_version=memory_version,
            validated_sources=validated_sources,
        )
        await db_session.commit()
    except IntegrityError as exc:
        await db_session.rollback()
        concurrent = await db_session.scalar(
            select(LlmMemory).where(
                LlmMemory.user_id == payload.user_id,
                LlmMemory.creation_idempotency_key == payload.idempotency_key,
            )
        )
        if concurrent is not None:
            return await _resolve_creation_replay(db_session, concurrent, request_hash)
        if active_semantic_hash is not None:
            active_memory = await db_session.scalar(
                select(LlmMemory).where(LlmMemory.active_semantic_key_hash == active_semantic_hash)
            )
            if active_memory is not None:
                raise ResourceConflictError("Active Memory semantic key conflict") from exc
        raise ResourceConflictError(
            "Memory creation conflicts with current resource state"
        ) from exc
    except OperationalError as exc:
        await db_session.rollback()
        if not _is_database_concurrency_conflict(exc):
            raise
        raise ResourceConflictError("Memory creation conflicted with a concurrent request") from exc
    return MemoryWriteResult(
        memory=await get_memory(db_session, memory_id),
        was_replayed=False,
    )


async def get_memory(db_session: AsyncSession, memory_id: str) -> MemoryRead:
    """Return one Memory with its current immutable version and source evidence."""

    memory = await db_session.get(LlmMemory, memory_id)
    if memory is None:
        raise ResourceNotFoundError("Memory")
    await db_session.refresh(memory)
    memory_version = await _require_current_memory_version(db_session, memory)
    await db_session.refresh(memory_version)
    return await _memory_read(db_session, memory, memory_version)


async def list_memories(
    db_session: AsyncSession,
    *,
    codec: CursorCodec,
    cursor: str | None,
    user_id: str,
    session_id: str | None,
    run_id: str | None,
    task_id: str | None,
    memory_type: MemoryType | None,
    memory_status: MemoryStatus | None,
    limit: int,
) -> CursorPage[MemorySummary]:
    """Return an owner-scoped, filter-bound page of current Memory versions."""

    resolved_scope = await validate_and_resolve_memory_scope(
        db_session,
        user_id=user_id,
        session_id=session_id,
        run_id=run_id,
        task_id=task_id,
    )
    query_fingerprint = database_query_fingerprint(
        "memories",
        {
            "user_id": user_id,
            "session_id": resolved_scope.session_id,
            "run_id": resolved_scope.run_id,
            "task_id": resolved_scope.task_id,
            "memory_type": memory_type.value if memory_type else None,
            "status": memory_status.value if memory_status else None,
        },
    )
    after_database_key = codec.decode_query(cursor) if cursor else None
    if after_database_key and after_database_key.query_fingerprint != query_fingerprint:
        raise InvalidCursorError
    database_page = await _query_memory_database_page(
        db_session,
        user_id=user_id,
        session_id=resolved_scope.session_id,
        run_id=resolved_scope.run_id,
        task_id=resolved_scope.task_id,
        memory_type=memory_type,
        memory_status=memory_status,
        after_database_key=after_database_key,
        query_fingerprint=query_fingerprint,
        limit=limit,
    )
    next_cursor = (
        codec.encode_query(database_page.next_database_key)
        if database_page.next_database_key
        else None
    )
    return CursorPage[MemorySummary](
        items=[_memory_summary(memory, version) for memory, version in database_page.items],
        next_cursor=next_cursor,
        has_more=next_cursor is not None,
        limit=limit,
    )


async def update_memory(
    db_session: AsyncSession,
    memory_id: str,
    payload: MemoryUpdate,
) -> MemoryWriteResult:
    """Apply a Memory mutation and convert MySQL lock failures to stable conflicts."""

    try:
        return await _update_memory_transaction(db_session, memory_id, payload)
    except OperationalError as exc:
        await db_session.rollback()
        if not _is_database_concurrency_conflict(exc):
            raise
        raise ResourceConflictError("Memory update conflicted with a concurrent request") from exc


async def _update_memory_transaction(
    db_session: AsyncSession,
    memory_id: str,
    payload: MemoryUpdate,
) -> MemoryWriteResult:
    """Execute one optimistic correction or state transition under database locks."""

    memory = await db_session.scalar(
        select(LlmMemory).where(LlmMemory.memory_id == memory_id).with_for_update()
    )
    if memory is None:
        raise ResourceNotFoundError("Memory")
    # PATCH omission and explicit null have different semantics for clearable fields.
    request_hash = hash_memory_request(payload.model_dump(mode="json", exclude_unset=True))
    replay_mutation = await db_session.scalar(
        select(LlmMemoryMutation).where(
            LlmMemoryMutation.memory_id == memory_id,
            LlmMemoryMutation.idempotency_key == payload.idempotency_key,
        )
    )
    if replay_mutation is not None:
        if replay_mutation.request_hash != request_hash:
            raise ResourceConflictError(
                "Memory mutation idempotency key was reused with another request"
            )
        return MemoryWriteResult(
            memory=await get_memory(db_session, memory_id),
            was_replayed=True,
        )
    if memory.status == MemoryStatus.DELETED:
        raise ResourceConflictError("Deleted Memory cannot be modified")
    if memory.current_version_number != payload.expected_version_number:
        raise ResourceConflictError("Memory version does not match expected_version_number")

    current_version = await _require_current_memory_version(db_session, memory)
    before_version_number = memory.current_version_number
    before_status = memory.status
    creates_version = bool(payload.model_fields_set & {"content_text", "importance", "confidence"})
    if creates_version:
        reject_sensitive_memory_content(payload.content_text or current_version.content_text or "")
    if creates_version and memory.status not in {
        MemoryStatus.CANDIDATE,
        MemoryStatus.ACTIVE,
    }:
        raise ResourceConflictError(f"Cannot correct Memory in {memory.status.value} status")

    if "expires_at" in payload.model_fields_set:
        memory.expires_at = payload.expires_at
    if "semantic_key" in payload.model_fields_set:
        memory.semantic_key = payload.semantic_key
    validate_memory_type_scope(
        memory_type=memory.memory_type,
        session_id=memory.session_id,
        run_id=memory.run_id,
        task_id=memory.task_id,
        expires_at=memory.expires_at,
        project_id=memory.project_id,
    )

    target_status = payload.status or memory.status
    validate_memory_status_transition(memory.status, target_status)
    active_semantic_hash = None
    if target_status == MemoryStatus.ACTIVE:
        active_semantic_hash = hash_active_semantic_key(
            user_id=memory.user_id,
            session_id=memory.session_id,
            run_id=memory.run_id,
            task_id=memory.task_id,
            project_id=memory.project_id,
            memory_type=memory.memory_type,
            semantic_key=memory.semantic_key,
        )
        await _supersede_replaced_memory(
            db_session,
            replacement_memory_id=memory.memory_id,
            replaced_memory_id=payload.supersedes_memory_id,
            expected_active_semantic_hash=active_semantic_hash,
            user_id=memory.user_id,
        )
        if memory.approval_method == ApprovalMethod.NONE:
            memory.approval_method = ApprovalMethod.TRUSTED_CALLER
            memory.approved_at = utc_now()
    memory.status = target_status
    memory.active_semantic_key_hash = active_semantic_hash
    if payload.supersedes_memory_id is not None:
        memory.supersedes_memory_id = payload.supersedes_memory_id

    new_version: LlmMemoryVersion | None = None
    validated_sources: list[ValidatedMemorySource] = []
    if creates_version:
        assert payload.sources is not None
        validated_sources = await validate_memory_sources(
            db_session,
            scope=ResolvedMemoryScope(
                user_id=memory.user_id,
                session_id=memory.session_id,
                run_id=memory.run_id,
                task_id=memory.task_id,
                project_id=memory.project_id,
            ),
            sources=payload.sources,
        )
        new_version_number = memory.current_version_number + 1
        new_version = _create_memory_version(
            memory_version_id=new_id(),
            memory_id=memory.memory_id,
            version_number=new_version_number,
            content_text=payload.content_text or current_version.content_text or "",
            importance=(
                payload.importance if payload.importance is not None else current_version.importance
            ),
            confidence=(
                payload.confidence if payload.confidence is not None else current_version.confidence
            ),
            created_by_type=MemoryCreatedByType.TRUSTED_CALLER,
            created_by_attempt_id=None,
        )
        memory.current_version_number = new_version_number
        db_session.add(new_version)
    changed_fields = sorted(
        field_name
        for field_name in payload.model_fields_set
        if field_name not in {"idempotency_key", "expected_version_number"}
    )
    if before_status != target_status and target_status == MemoryStatus.ACTIVE:
        operation = MemoryMutationOperation.ACTIVATE
    elif before_status != target_status and target_status == MemoryStatus.REJECTED:
        operation = MemoryMutationOperation.REJECT
    elif before_status != target_status and target_status == MemoryStatus.SUPERSEDED:
        operation = MemoryMutationOperation.SUPERSEDE
    elif creates_version:
        operation = MemoryMutationOperation.CORRECT
    else:
        operation = MemoryMutationOperation.UPDATE_METADATA
    db_session.add(
        LlmMemoryMutation(
            memory_id=memory.memory_id,
            idempotency_key=payload.idempotency_key,
            request_hash=request_hash,
            operation=operation,
            actor_type=MemoryMutationActorType.TRUSTED_CALLER,
            actor_id=None,
            before_version_number=before_version_number,
            after_version_number=memory.current_version_number,
            before_status=before_status,
            after_status=memory.status,
            change_json={
                "changed_fields": changed_fields,
                "creates_version": creates_version,
            },
            result_version_number=memory.current_version_number,
            result_status=memory.status,
        )
    )
    try:
        if new_version is not None:
            await db_session.flush()
            _add_memory_source_and_term_rows(
                db_session,
                memory_version=new_version,
                validated_sources=validated_sources,
            )
        await db_session.commit()
    except IntegrityError as exc:
        await db_session.rollback()
        raise ResourceConflictError("Memory update conflicts with current state") from exc
    return MemoryWriteResult(
        memory=await get_memory(db_session, memory_id),
        was_replayed=False,
    )


async def delete_memory(db_session: AsyncSession, memory_id: str) -> MemoryRead:
    """Delete Memory content and convert MySQL lock failures to stable conflicts."""

    try:
        return await _delete_memory_transaction(db_session, memory_id)
    except OperationalError as exc:
        await db_session.rollback()
        if not _is_database_concurrency_conflict(exc):
            raise
        raise ResourceConflictError("Memory deletion conflicted with a concurrent request") from exc


async def _delete_memory_transaction(
    db_session: AsyncSession,
    memory_id: str,
) -> MemoryRead:
    """Idempotently erase one locked Memory's inline text and search terms."""

    memory = await db_session.scalar(
        select(LlmMemory).where(LlmMemory.memory_id == memory_id).with_for_update()
    )
    if memory is None:
        raise ResourceNotFoundError("Memory")
    if memory.status == MemoryStatus.DELETED:
        return await get_memory(db_session, memory_id)
    erased_at = utc_now()
    before_version_number = memory.current_version_number
    before_status = memory.status
    memory.status = MemoryStatus.DELETED
    memory.deleted_at = erased_at
    memory.active_semantic_key_hash = None
    memory.approval_method = ApprovalMethod.NONE
    memory.approved_at = None
    memory.is_core_profile_eligible = False
    version_ids = select(LlmMemoryVersion.memory_version_id).where(
        LlmMemoryVersion.memory_id == memory_id
    )
    await db_session.execute(
        delete(LlmMemorySearchTerm).where(LlmMemorySearchTerm.memory_version_id.in_(version_ids))
    )
    await db_session.execute(
        update(LlmMemoryVersion)
        .where(LlmMemoryVersion.memory_id == memory_id)
        .values(
            content_text=None,
            estimated_token_count=0,
            unique_search_term_count=0,
            content_erased_at=erased_at,
        )
    )
    db_session.add(
        LlmMemoryMutation(
            memory_id=memory_id,
            idempotency_key=f"delete:{memory_id}",
            request_hash=hashlib.sha256(f"delete:{memory_id}".encode()).hexdigest(),
            operation=MemoryMutationOperation.DELETE,
            actor_type=MemoryMutationActorType.TRUSTED_CALLER,
            actor_id=None,
            before_version_number=before_version_number,
            after_version_number=before_version_number,
            before_status=before_status,
            after_status=MemoryStatus.DELETED,
            change_json=None,
            result_version_number=before_version_number,
            result_status=MemoryStatus.DELETED,
        )
    )
    await db_session.commit()
    return await get_memory(db_session, memory_id)


async def list_memory_versions(
    db_session: AsyncSession,
    memory_id: str,
    *,
    codec: CursorCodec,
    cursor: str | None,
    limit: int,
) -> CursorPage[MemoryVersionRead]:
    """Return immutable Memory versions in ascending version order with bounded sources."""

    memory = await db_session.get(LlmMemory, memory_id)
    if memory is None:
        raise ResourceNotFoundError("Memory")
    after_database_key = codec.decode_sequence(cursor) if cursor else None
    if after_database_key and after_database_key.scope_id != memory_id:
        raise InvalidCursorError
    statement = select(LlmMemoryVersion).where(LlmMemoryVersion.memory_id == memory_id)
    if after_database_key is not None:
        statement = statement.where(LlmMemoryVersion.version_number > after_database_key.sequence)
    versions = list(
        (
            await db_session.scalars(
                statement.order_by(LlmMemoryVersion.version_number).limit(limit + 1)
            )
        ).all()
    )
    has_more = len(versions) > limit
    items = versions[:limit]
    next_cursor = None
    if has_more and items:
        next_cursor = codec.encode_sequence(
            DatabaseSequencePaginationKey(
                scope_id=memory_id,
                sequence=items[-1].version_number,
            )
        )
    return CursorPage[MemoryVersionRead](
        items=[await _memory_version_read(db_session, version) for version in items],
        next_cursor=next_cursor,
        has_more=next_cursor is not None,
        limit=limit,
    )


async def validate_and_resolve_memory_scope(
    db_session: AsyncSession,
    *,
    user_id: str,
    session_id: str | None,
    run_id: str | None,
    task_id: str | None,
    project_id: str | None = None,
) -> ResolvedMemoryScope:
    """Validate nested resource ownership and return scope inherited from Task and Run."""

    user = await db_session.get(User, user_id)
    if user is None:
        raise ResourceNotFoundError("User")
    if not user.is_active:
        raise ResourceConflictError("Memory user must be active")

    resolved_project_id = project_id
    if project_id is not None:
        project = await db_session.get(LlmProject, project_id)
        if project is None:
            raise ResourceNotFoundError("Project")
        if project.owner_user_id != user_id:
            raise ResourceConflictError("Memory project does not belong to the user")

    resolved_session_id = session_id
    resolved_run_id = run_id
    if session_id is not None:
        conversation = await db_session.get(LlmSession, session_id)
        if conversation is None:
            raise ResourceNotFoundError("Session")
        if conversation.user_id != user_id:
            raise ResourceConflictError("Memory session does not belong to the user")
        if project_id is not None and conversation.project_id != project_id:
            raise ResourceConflictError("Memory session does not belong to the project")
    if run_id is not None:
        run = await db_session.get(LlmRun, run_id)
        if run is None:
            raise ResourceNotFoundError("Run")
        if run.user_id != user_id:
            raise ResourceConflictError("Memory run does not belong to the user")
        if session_id is not None and run.session_id != session_id:
            raise ResourceConflictError("Memory run does not belong to the session")
        if project_id is not None and run.project_id != project_id:
            raise ResourceConflictError("Memory run does not belong to the project")
        resolved_session_id = resolved_session_id or run.session_id
    if task_id is not None:
        task = await db_session.get(LlmTask, task_id)
        if task is None:
            raise ResourceNotFoundError("Task")
        task_run = await db_session.get(LlmRun, task.run_id)
        if task_run is None or task_run.user_id != user_id:
            raise ResourceConflictError("Memory task does not belong to the user")
        if run_id is not None and task.run_id != run_id:
            raise ResourceConflictError("Memory task does not belong to the run")
        if session_id is not None and task_run.session_id != session_id:
            raise ResourceConflictError("Memory task does not belong to the session")
        if project_id is not None and task_run.project_id != project_id:
            raise ResourceConflictError("Memory task does not belong to the project")
        resolved_run_id = resolved_run_id or task.run_id
        resolved_session_id = resolved_session_id or task_run.session_id
    return ResolvedMemoryScope(
        user_id=user_id,
        session_id=resolved_session_id,
        run_id=resolved_run_id,
        task_id=task_id,
        project_id=resolved_project_id,
    )


async def validate_memory_sources(
    db_session: AsyncSession,
    *,
    scope: ResolvedMemoryScope,
    sources: list[MemorySourceCreate],
) -> list[ValidatedMemorySource]:
    """Validate each evidence locator belongs to the Memory owner and hash its content."""

    source_keys = {(source.source_type, source.source_resource_id) for source in sources}
    if len(source_keys) != len(sources):
        raise InvalidRequestError("Memory sources must not contain duplicates")
    validated_sources: list[ValidatedMemorySource] = []
    for source in sources:
        validated_source = await _validated_memory_source(
            db_session,
            user_id=scope.user_id,
            source=source,
        )
        _validate_source_scope(validated_source, scope)
        validated_sources.append(
            ValidatedMemorySource(
                source_type=validated_source.source_type,
                source_resource_id=validated_source.source_resource_id,
                source_content_hash=validated_source.source_content_hash,
                session_id=validated_source.session_id,
                run_id=validated_source.run_id,
                task_id=validated_source.task_id,
                trust_level=source_trust_level(source.source_type, source.trust_level),
            )
        )
    return validated_sources


async def _validated_memory_source(
    db_session: AsyncSession,
    *,
    user_id: str,
    source: MemorySourceCreate,
) -> ValidatedMemorySource:
    """Resolve one typed source and return hashed evidence with its actual resource scope."""

    if source.source_type == MemorySourceType.TRUSTED_REQUEST:
        return ValidatedMemorySource(
            source_type=source.source_type,
            source_resource_id=source.source_resource_id,
            source_content_hash=hashlib.sha256(source.source_resource_id.encode()).hexdigest(),
            session_id=None,
            run_id=None,
            task_id=None,
        )
    if source.source_type == MemorySourceType.MESSAGE:
        message = await db_session.get(LlmMessage, source.source_resource_id)
        if message is None:
            raise ResourceNotFoundError("Memory source message")
        conversation = await db_session.get(LlmSession, message.session_id)
        if conversation is None or conversation.user_id != user_id:
            raise ResourceConflictError("Memory source does not belong to the user")
        return ValidatedMemorySource(
            source_type=source.source_type,
            source_resource_id=source.source_resource_id,
            source_content_hash=hash_memory_request(
                {
                    "message_id": message.message_id,
                    "content_text": message.content_text,
                    "content_uri": message.content_uri,
                    "created_at": message.created_at,
                }
            ),
            session_id=message.session_id,
            run_id=message.run_id,
            task_id=None,
        )
    if source.source_type == MemorySourceType.MODEL_ATTEMPT:
        attempt = await _validate_attempt_owner(
            db_session,
            attempt_id=source.source_resource_id,
            user_id=user_id,
        )
        run = await db_session.get(LlmRun, attempt.run_id)
        assert run is not None
        return ValidatedMemorySource(
            source_type=source.source_type,
            source_resource_id=source.source_resource_id,
            source_content_hash=hash_memory_request(
                {
                    "attempt_id": attempt.attempt_id,
                    "provider_request_id": attempt.provider_request_id,
                    "raw_response_uri": attempt.raw_response_uri,
                    "completed_at": attempt.completed_at,
                }
            ),
            session_id=run.session_id,
            run_id=attempt.run_id,
            task_id=attempt.task_id,
        )
    if source.source_type == MemorySourceType.ARTIFACT:
        artifact = await db_session.get(LlmRunArtifact, source.source_resource_id)
        if artifact is None:
            raise ResourceNotFoundError("Memory source Artifact")
        run = await db_session.get(LlmRun, artifact.run_id)
        if run is None or run.user_id != user_id:
            raise ResourceConflictError("Memory source does not belong to the user")
        return ValidatedMemorySource(
            source_type=source.source_type,
            source_resource_id=source.source_resource_id,
            source_content_hash=artifact.content_hash,
            session_id=run.session_id,
            run_id=artifact.run_id,
            task_id=artifact.task_id,
        )
    if source.source_type == MemorySourceType.TOOL_CALL:
        tool_call = await db_session.get(LlmModelToolCall, source.source_resource_id)
        if tool_call is None:
            raise ResourceNotFoundError("Memory source tool call")
        await _validate_attempt_owner(
            db_session,
            attempt_id=tool_call.attempt_id,
            user_id=user_id,
        )
        attempt = await db_session.get(LlmModelAttempt, tool_call.attempt_id)
        assert attempt is not None
        run = await db_session.get(LlmRun, attempt.run_id)
        assert run is not None
        return ValidatedMemorySource(
            source_type=source.source_type,
            source_resource_id=source.source_resource_id,
            source_content_hash=hash_memory_request(
                {
                    "tool_call_id": tool_call.tool_call_id,
                    "input_json": tool_call.input_json,
                    "result_uri": tool_call.result_uri,
                    "status": tool_call.status,
                }
            ),
            session_id=run.session_id,
            run_id=attempt.run_id,
            task_id=attempt.task_id,
        )
    raise InvalidRequestError("Unsupported Memory source type")


def _validate_source_scope(
    source: ValidatedMemorySource,
    scope: ResolvedMemoryScope,
) -> None:
    """Reject evidence that cannot prove the Memory's declared nested scope."""

    if scope.session_id is not None and source.session_id != scope.session_id:
        raise ResourceConflictError("Memory source does not belong to the Memory scope")
    if scope.run_id is not None and source.run_id != scope.run_id:
        raise ResourceConflictError("Memory source does not belong to the Memory scope")
    if scope.task_id is not None and source.task_id != scope.task_id:
        raise ResourceConflictError("Memory source does not belong to the Memory scope")


async def _validate_attempt_owner(
    db_session: AsyncSession,
    *,
    attempt_id: str,
    user_id: str,
) -> LlmModelAttempt:
    """Return one model attempt after proving its Run belongs to the Memory owner."""

    attempt = await db_session.get(LlmModelAttempt, attempt_id)
    if attempt is None:
        raise ResourceNotFoundError("Memory source model attempt")
    run = await db_session.get(LlmRun, attempt.run_id)
    if run is None or run.user_id != user_id:
        raise ResourceConflictError("Memory source does not belong to the user")
    return attempt


def _create_memory_version(
    *,
    memory_version_id: str,
    memory_id: str,
    version_number: int,
    content_text: str,
    importance: object,
    confidence: object,
    created_by_type: MemoryCreatedByType,
    created_by_attempt_id: str | None,
) -> LlmMemoryVersion:
    """Build one immutable Memory version with hashes, token estimate, and term count."""

    content_hash, normalized_content_hash = hash_memory_content(content_text)
    search_term_counts = build_memory_search_term_counts(content_text)
    return LlmMemoryVersion(
        memory_version_id=memory_version_id,
        memory_id=memory_id,
        version_number=version_number,
        content_text=content_text,
        content_hash=content_hash,
        normalized_content_hash=normalized_content_hash,
        importance=importance,
        confidence=confidence,
        estimated_token_count=estimate_memory_tokens(content_text),
        token_estimator_version=MEMORY_TOKEN_ESTIMATOR_VERSION,
        unique_search_term_count=len(search_term_counts),
        created_by_type=created_by_type,
        created_by_attempt_id=created_by_attempt_id,
    )


def _add_memory_source_and_term_rows(
    db_session: AsyncSession,
    *,
    memory_version: LlmMemoryVersion,
    validated_sources: list[ValidatedMemorySource],
) -> None:
    """Stage validated source and deterministic term rows beside a Memory version."""

    assert memory_version.content_text is not None
    db_session.add_all(
        [
            LlmMemorySource(
                memory_version_id=memory_version.memory_version_id,
                source_type=source.source_type,
                source_resource_id=source.source_resource_id,
                source_content_hash=source.source_content_hash,
                trust_level=source.trust_level,
                source_order=source_order,
            )
            for source_order, source in enumerate(validated_sources, start=1)
        ]
    )
    db_session.add_all(
        [
            LlmMemorySearchTerm(
                memory_version_id=memory_version.memory_version_id,
                term_hash=term_hash,
                term_frequency=term_frequency,
            )
            for term_hash, term_frequency in build_memory_search_term_counts(
                memory_version.content_text
            ).items()
        ]
    )


async def _supersede_replaced_memory(
    db_session: AsyncSession,
    *,
    replacement_memory_id: str,
    replaced_memory_id: str | None,
    expected_active_semantic_hash: str | None,
    user_id: str,
) -> None:
    """Replace one explicitly named active semantic fact without silent conflict merging."""

    if replaced_memory_id is None:
        # The unique semantic-key constraint arbitrates ordinary activation. Locking a
        # missing index key here creates an InnoDB gap-lock deadlock under concurrent inserts.
        return
    if expected_active_semantic_hash is None:
        raise ResourceConflictError("Superseded Memory is not the active semantic fact")
    existing = await db_session.scalar(
        select(LlmMemory)
        .where(
            LlmMemory.active_semantic_key_hash == expected_active_semantic_hash,
            LlmMemory.memory_id != replacement_memory_id,
        )
        .with_for_update()
    )
    if existing is None:
        raise ResourceConflictError("Superseded Memory is not the active semantic fact")
    if replaced_memory_id != existing.memory_id:
        raise ResourceConflictError("Active Memory semantic key conflict")
    if existing.user_id != user_id or existing.status != MemoryStatus.ACTIVE:
        raise ResourceConflictError("Superseded Memory is not active in the same owner scope")
    existing.status = MemoryStatus.SUPERSEDED
    existing.active_semantic_key_hash = None


async def _resolve_creation_replay(
    db_session: AsyncSession,
    memory: LlmMemory,
    request_hash: str,
) -> MemoryWriteResult:
    """Return an identical create result or reject idempotency-key payload drift."""

    if memory.creation_request_hash != request_hash:
        raise ResourceConflictError("Memory idempotency key was reused with another request")
    return MemoryWriteResult(
        memory=await get_memory(db_session, memory.memory_id),
        was_replayed=True,
    )


async def _require_current_memory_version(
    db_session: AsyncSession,
    memory: LlmMemory,
) -> LlmMemoryVersion:
    """Return the current Memory version or report corrupted version metadata."""

    version = await db_session.scalar(
        select(LlmMemoryVersion).where(
            LlmMemoryVersion.memory_id == memory.memory_id,
            LlmMemoryVersion.version_number == memory.current_version_number,
        )
    )
    if version is None:
        raise ResourceConflictError("Memory current version is unavailable")
    return version


async def _memory_read(
    db_session: AsyncSession,
    memory: LlmMemory,
    version: LlmMemoryVersion,
) -> MemoryRead:
    """Map current ORM facts and ordered sources to the public Memory response."""

    sources = list(
        (
            await db_session.scalars(
                select(LlmMemorySource)
                .where(LlmMemorySource.memory_version_id == version.memory_version_id)
                .order_by(LlmMemorySource.source_order)
            )
        ).all()
    )
    return MemoryRead(
        memory_id=memory.memory_id,
        user_id=memory.user_id,
        session_id=memory.session_id,
        run_id=memory.run_id,
        task_id=memory.task_id,
        project_id=memory.project_id,
        memory_type=memory.memory_type,
        status=memory.status,
        version_number=version.version_number,
        content_text=version.content_text,
        content_hash=version.content_hash,
        importance=version.importance,
        confidence=version.confidence,
        estimated_token_count=version.estimated_token_count,
        semantic_key=memory.semantic_key,
        supersedes_memory_id=memory.supersedes_memory_id,
        approval_method=memory.approval_method,
        approved_at=memory.approved_at,
        sensitivity_classification=memory.sensitivity_classification,
        is_core_profile_eligible=memory.is_core_profile_eligible,
        expires_at=memory.expires_at,
        deleted_at=memory.deleted_at,
        sources=[MemorySourceRead.model_validate(source) for source in sources],
        created_at=memory.created_at,
        updated_at=memory.updated_at,
    )


async def _memory_version_read(
    db_session: AsyncSession,
    version: LlmMemoryVersion,
) -> MemoryVersionRead:
    """Map one immutable version and its ordered evidence sources to HTTP output."""

    sources = list(
        (
            await db_session.scalars(
                select(LlmMemorySource)
                .where(LlmMemorySource.memory_version_id == version.memory_version_id)
                .order_by(LlmMemorySource.source_order)
            )
        ).all()
    )
    return MemoryVersionRead(
        memory_version_id=version.memory_version_id,
        memory_id=version.memory_id,
        version_number=version.version_number,
        content_text=version.content_text,
        content_hash=version.content_hash,
        importance=version.importance,
        confidence=version.confidence,
        estimated_token_count=version.estimated_token_count,
        token_estimator_version=version.token_estimator_version,
        created_by_type=version.created_by_type,
        created_by_attempt_id=version.created_by_attempt_id,
        content_erased_at=version.content_erased_at,
        created_at=version.created_at,
        sources=[MemorySourceRead.model_validate(source) for source in sources],
    )


def _memory_summary(memory: LlmMemory, version: LlmMemoryVersion) -> MemorySummary:
    """Build one bounded Memory list item from its current version."""

    return MemorySummary(
        memory_id=memory.memory_id,
        user_id=memory.user_id,
        session_id=memory.session_id,
        run_id=memory.run_id,
        task_id=memory.task_id,
        project_id=memory.project_id,
        memory_type=memory.memory_type,
        status=memory.status,
        version_number=version.version_number,
        content_preview=version.content_text[:200] if version.content_text else None,
        importance=version.importance,
        confidence=version.confidence,
        semantic_key=memory.semantic_key,
        expires_at=memory.expires_at,
        deleted_at=memory.deleted_at,
        created_at=memory.created_at,
        updated_at=memory.updated_at,
    )


def _is_database_concurrency_conflict(error: OperationalError) -> bool:
    """Identify MySQL deadlock and lock-timeout errors that are safe to retry idempotently."""

    original_error = error.orig
    error_arguments = getattr(original_error, "args", ())
    return bool(error_arguments and error_arguments[0] in {1205, 1213})


async def _query_memory_database_page(
    db_session: AsyncSession,
    *,
    user_id: str,
    session_id: str | None,
    run_id: str | None,
    task_id: str | None,
    memory_type: MemoryType | None,
    memory_status: MemoryStatus | None,
    after_database_key: DatabaseQueryPaginationKey | None,
    query_fingerprint: str,
    limit: int,
) -> MemoryDatabasePage:
    """Query one stable Memory page joined to each row's current version."""

    statement = (
        select(LlmMemory, LlmMemoryVersion)
        .join(
            LlmMemoryVersion,
            and_(
                LlmMemoryVersion.memory_id == LlmMemory.memory_id,
                LlmMemoryVersion.version_number == LlmMemory.current_version_number,
            ),
        )
        .where(LlmMemory.user_id == user_id)
    )
    if session_id is not None:
        statement = statement.where(LlmMemory.session_id == session_id)
    if run_id is not None:
        statement = statement.where(LlmMemory.run_id == run_id)
    if task_id is not None:
        statement = statement.where(LlmMemory.task_id == task_id)
    if memory_type is not None:
        statement = statement.where(LlmMemory.memory_type == memory_type)
    if memory_status is not None:
        statement = statement.where(LlmMemory.status == memory_status)
    else:
        statement = statement.where(LlmMemory.status != MemoryStatus.DELETED)
    if after_database_key is not None:
        cursor_time = after_database_key.created_at.astimezone(UTC).replace(tzinfo=None)
        statement = statement.where(
            or_(
                LlmMemory.created_at > cursor_time,
                and_(
                    LlmMemory.created_at == cursor_time,
                    LlmMemory.memory_id > after_database_key.identifier,
                ),
            )
        )
    rows = list(
        (
            await db_session.execute(
                statement.order_by(LlmMemory.created_at, LlmMemory.memory_id).limit(limit + 1)
            )
        ).all()
    )
    has_more = len(rows) > limit
    items = [(row[0], row[1]) for row in rows[:limit]]
    next_database_key = None
    if has_more and items:
        last_memory = items[-1][0]
        next_database_key = DatabaseQueryPaginationKey(
            created_at=last_memory.created_at,
            identifier=last_memory.memory_id,
            query_fingerprint=query_fingerprint,
        )
    return MemoryDatabasePage(items=items, next_database_key=next_database_key)


async def list_memory_mutations(
    db_session: AsyncSession,
    memory_id: str,
    *,
    codec: CursorCodec,
    cursor: str | None,
    limit: int,
) -> CursorPage[MemoryMutationRead]:
    """Return the immutable mutation audit ledger for one Memory in creation order."""

    memory = await db_session.get(LlmMemory, memory_id)
    if memory is None:
        raise ResourceNotFoundError("Memory")
    after_database_key = codec.decode_sequence(cursor) if cursor else None
    if after_database_key and after_database_key.scope_id != memory_id:
        raise InvalidCursorError
    statement = select(LlmMemoryMutation).where(LlmMemoryMutation.memory_id == memory_id)
    if after_database_key is not None:
        cursor_time = after_database_key.created_at.astimezone(UTC).replace(tzinfo=None)
        statement = statement.where(
            or_(
                LlmMemoryMutation.created_at > cursor_time,
                and_(
                    LlmMemoryMutation.created_at == cursor_time,
                    LlmMemoryMutation.memory_mutation_id > after_database_key.identifier,
                ),
            )
        )
    mutations = list(
        (
            await db_session.scalars(
                statement.order_by(
                    LlmMemoryMutation.created_at, LlmMemoryMutation.memory_mutation_id
                ).limit(limit + 1)
            )
        ).all()
    )
    has_more = len(mutations) > limit
    items = mutations[:limit]
    next_cursor = None
    if has_more and items:
        last_mutation = items[-1]
        next_database_key = DatabaseQueryPaginationKey(
            created_at=last_mutation.created_at,
            identifier=last_mutation.memory_mutation_id,
            query_fingerprint=memory_id,
        )
        next_cursor = codec.encode_query(next_database_key)
    return CursorPage[MemoryMutationRead](
        items=[MemoryMutationRead.model_validate(item) for item in items],
        next_cursor=next_cursor,
        has_more=next_cursor is not None,
        limit=limit,
    )
