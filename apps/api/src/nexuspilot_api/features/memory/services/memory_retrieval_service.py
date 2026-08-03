"""Deterministic owner-scoped Memory candidate search, ranking, and evidence persistence."""

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from nexuspilot_api.core.errors import (
    InvalidRequestError,
    ResourceConflictError,
    ResourceNotFoundError,
)
from nexuspilot_api.features.memory.schemas.memories import (
    MemoryRetrievalCreate,
    MemoryRetrievalRead,
    MemoryRetrievalResultRead,
)
from nexuspilot_api.features.memory.services.memory_policy import (
    MAX_MEMORY_QUERY_TERMS,
    MEMORY_CANDIDATE_METHOD,
    MEMORY_RANKER_VERSION,
    MemoryRankComponents,
    build_memory_search_term_counts,
    calculate_memory_rank,
    hash_memory_content,
    hash_memory_request,
)
from nexuspilot_api.features.memory.services.memory_service import (
    ResolvedMemoryScope,
    validate_and_resolve_memory_scope,
)
from nexuspilot_api.models import (
    LlmMemory,
    LlmMemoryRetrieval,
    LlmMemoryRetrievalResult,
    LlmMemorySearchTerm,
    LlmMemoryVersion,
    MemoryRetrievalStatus,
    MemoryStatus,
)
from nexuspilot_api.models.base import utc_now

MAX_MEMORY_DATABASE_CANDIDATES = 500


@dataclass(frozen=True)
class MemoryRetrievalWriteResult:
    """Return one retrieval response and whether an idempotent request replayed."""

    retrieval: MemoryRetrievalRead
    was_replayed: bool


@dataclass(frozen=True)
class RankedMemoryCandidate:
    """Contain a current Memory version and its deterministic rank components."""

    memory: LlmMemory
    version: LlmMemoryVersion
    scores: MemoryRankComponents


async def retrieve_memories(
    db_session: AsyncSession,
    payload: MemoryRetrievalCreate,
) -> MemoryRetrievalWriteResult:
    """Retrieve bounded active Memory facts and persist complete ranking evidence."""

    resolved_scope = await validate_and_resolve_memory_scope(
        db_session,
        user_id=payload.user_id,
        session_id=payload.session_id,
        run_id=payload.run_id,
        task_id=payload.task_id,
    )
    request_hash = hash_memory_request(payload.model_dump(mode="json"))
    existing = await db_session.scalar(
        select(LlmMemoryRetrieval).where(
            LlmMemoryRetrieval.user_id == payload.user_id,
            LlmMemoryRetrieval.idempotency_key == payload.idempotency_key,
        )
    )
    if existing is not None:
        return await _resolve_retrieval_replay(db_session, existing, request_hash)

    query_term_counts = build_memory_search_term_counts(
        payload.query_text,
        maximum_unique_terms=MAX_MEMORY_QUERY_TERMS,
    )
    if not query_term_counts:
        raise InvalidRequestError("Memory retrieval query has no searchable terms")
    retrieval_time = utc_now()
    candidates = await _query_ranked_candidates(
        db_session,
        payload=payload,
        resolved_scope=resolved_scope,
        query_term_hashes=list(query_term_counts),
        retrieval_time=retrieval_time,
    )
    ranked_candidates = candidates[: payload.limit]
    retrieval = LlmMemoryRetrieval(
        memory_retrieval_id=_new_retrieval_id(),
        user_id=payload.user_id,
        session_id=resolved_scope.session_id,
        run_id=resolved_scope.run_id,
        task_id=resolved_scope.task_id,
        query_text=payload.query_text,
        query_hash=hash_memory_content(payload.query_text)[0],
        memory_types_json=[memory_type.value for memory_type in payload.memory_types],
        result_limit=payload.limit,
        token_budget=payload.token_budget,
        candidate_method=MEMORY_CANDIDATE_METHOD,
        ranker_version=MEMORY_RANKER_VERSION,
        status=MemoryRetrievalStatus.COMPLETED,
        idempotency_key=payload.idempotency_key,
        request_hash=request_hash,
        created_at=retrieval_time,
        completed_at=retrieval_time,
    )
    db_session.add(retrieval)
    selected_token_count = 0
    for rank, candidate in enumerate(ranked_candidates, start=1):
        can_select = (
            selected_token_count + candidate.version.estimated_token_count <= payload.token_budget
        )
        if can_select:
            selected_token_count += candidate.version.estimated_token_count
        db_session.add(
            LlmMemoryRetrievalResult(
                memory_retrieval_id=retrieval.memory_retrieval_id,
                memory_id=candidate.memory.memory_id,
                memory_version_id=candidate.version.memory_version_id,
                rank=rank,
                total_score=candidate.scores.total_score,
                lexical_score=candidate.scores.lexical_score,
                scope_score=candidate.scores.scope_score,
                importance_score=candidate.scores.importance_score,
                confidence_score=candidate.scores.confidence_score,
                recency_score=candidate.scores.recency_score,
                estimated_token_count=candidate.version.estimated_token_count,
                selected=can_select,
                exclusion_reason=None if can_select else "token_budget_exceeded",
            )
        )
    try:
        await db_session.commit()
    except IntegrityError as exc:
        await db_session.rollback()
        concurrent = await db_session.scalar(
            select(LlmMemoryRetrieval).where(
                LlmMemoryRetrieval.user_id == payload.user_id,
                LlmMemoryRetrieval.idempotency_key == payload.idempotency_key,
            )
        )
        if concurrent is not None:
            return await _resolve_retrieval_replay(db_session, concurrent, request_hash)
        raise ResourceConflictError("Memory retrieval could not be persisted") from exc
    return MemoryRetrievalWriteResult(
        retrieval=await get_memory_retrieval(
            db_session,
            retrieval.memory_retrieval_id,
        ),
        was_replayed=False,
    )


async def get_memory_retrieval(
    db_session: AsyncSession,
    memory_retrieval_id: str,
) -> MemoryRetrievalRead:
    """Return one persisted Memory retrieval and its ordered immutable evidence."""

    retrieval = await db_session.get(LlmMemoryRetrieval, memory_retrieval_id)
    if retrieval is None:
        raise ResourceNotFoundError("Memory retrieval")
    rows = list(
        (
            await db_session.execute(
                select(
                    LlmMemoryRetrievalResult,
                    LlmMemory,
                    LlmMemoryVersion,
                )
                .join(
                    LlmMemory,
                    LlmMemory.memory_id == LlmMemoryRetrievalResult.memory_id,
                )
                .join(
                    LlmMemoryVersion,
                    LlmMemoryVersion.memory_version_id
                    == LlmMemoryRetrievalResult.memory_version_id,
                )
                .where(LlmMemoryRetrievalResult.memory_retrieval_id == memory_retrieval_id)
                .order_by(LlmMemoryRetrievalResult.rank)
            )
        ).all()
    )
    return MemoryRetrievalRead(
        memory_retrieval_id=retrieval.memory_retrieval_id,
        user_id=retrieval.user_id,
        session_id=retrieval.session_id,
        run_id=retrieval.run_id,
        task_id=retrieval.task_id,
        query_text=retrieval.query_text,
        query_hash=retrieval.query_hash,
        memory_types=list(retrieval.memory_types_json),
        limit=retrieval.result_limit,
        token_budget=retrieval.token_budget,
        candidate_method=retrieval.candidate_method,
        ranker_version=retrieval.ranker_version,
        idempotency_key=retrieval.idempotency_key,
        created_at=_aware_utc(retrieval.created_at),
        completed_at=_aware_utc(retrieval.completed_at),
        results=[
            MemoryRetrievalResultRead(
                rank=result.rank,
                memory_id=memory.memory_id,
                memory_version_id=version.memory_version_id,
                memory_type=memory.memory_type,
                content_text=version.content_text,
                total_score=result.total_score,
                lexical_score=result.lexical_score,
                scope_score=result.scope_score,
                importance_score=result.importance_score,
                confidence_score=result.confidence_score,
                recency_score=result.recency_score,
                estimated_token_count=result.estimated_token_count,
                selected=result.selected,
                exclusion_reason=result.exclusion_reason,
            )
            for result, memory, version in rows
        ],
    )


async def _query_ranked_candidates(
    db_session: AsyncSession,
    *,
    payload: MemoryRetrievalCreate,
    resolved_scope: ResolvedMemoryScope,
    query_term_hashes: list[str],
    retrieval_time: datetime,
) -> list[RankedMemoryCandidate]:
    """Load bounded lexical candidates, verify scope, and apply the versioned ranker."""

    matched_terms = (
        select(
            LlmMemorySearchTerm.memory_version_id.label("memory_version_id"),
            func.count(LlmMemorySearchTerm.term_hash).label("matched_term_count"),
        )
        .where(LlmMemorySearchTerm.term_hash.in_(query_term_hashes))
        .group_by(LlmMemorySearchTerm.memory_version_id)
        .subquery()
    )
    statement = (
        select(
            LlmMemory,
            LlmMemoryVersion,
            matched_terms.c.matched_term_count,
        )
        .join(
            LlmMemoryVersion,
            and_(
                LlmMemoryVersion.memory_id == LlmMemory.memory_id,
                LlmMemoryVersion.version_number == LlmMemory.current_version_number,
            ),
        )
        .join(
            matched_terms,
            matched_terms.c.memory_version_id == LlmMemoryVersion.memory_version_id,
        )
        .where(
            LlmMemory.user_id == resolved_scope.user_id,
            LlmMemory.status == MemoryStatus.ACTIVE,
            or_(
                LlmMemory.expires_at.is_(None),
                LlmMemory.expires_at > _database_time(retrieval_time),
            ),
        )
    )
    statement = _apply_scope_filters(statement, resolved_scope)
    if payload.memory_types:
        statement = statement.where(LlmMemory.memory_type.in_(payload.memory_types))
    rows = list(
        (
            await db_session.execute(
                statement.order_by(
                    matched_terms.c.matched_term_count.desc(),
                    LlmMemory.updated_at.desc(),
                    LlmMemory.memory_id,
                ).limit(MAX_MEMORY_DATABASE_CANDIDATES)
            )
        ).all()
    )
    candidates: list[RankedMemoryCandidate] = []
    for memory, version, matched_term_count in rows:
        age_days = max(
            0,
            int((retrieval_time - _aware_utc(version.created_at)).total_seconds() // 86_400),
        )
        candidates.append(
            RankedMemoryCandidate(
                memory=memory,
                version=version,
                scores=calculate_memory_rank(
                    matched_term_count=int(matched_term_count),
                    query_term_count=len(query_term_hashes),
                    memory_term_count=version.unique_search_term_count,
                    scope_score=_memory_scope_score(memory, resolved_scope),
                    importance=version.importance,
                    confidence=version.confidence,
                    age_days=age_days,
                ),
            )
        )
    return sorted(
        candidates,
        key=lambda candidate: (
            -candidate.scores.total_score,
            -_aware_utc(candidate.memory.updated_at).timestamp(),
            candidate.memory.memory_id,
        ),
    )


def _apply_scope_filters(statement: object, scope: ResolvedMemoryScope) -> object:
    """Restrict candidates to global facts or facts nested inside the requested scope."""

    if scope.session_id is None:
        statement = statement.where(LlmMemory.session_id.is_(None))
    else:
        statement = statement.where(
            or_(LlmMemory.session_id.is_(None), LlmMemory.session_id == scope.session_id)
        )
    if scope.run_id is None:
        statement = statement.where(LlmMemory.run_id.is_(None))
    else:
        statement = statement.where(
            or_(LlmMemory.run_id.is_(None), LlmMemory.run_id == scope.run_id)
        )
    if scope.task_id is None:
        statement = statement.where(LlmMemory.task_id.is_(None))
    else:
        statement = statement.where(
            or_(LlmMemory.task_id.is_(None), LlmMemory.task_id == scope.task_id)
        )
    return statement


def _memory_scope_score(memory: LlmMemory, scope: ResolvedMemoryScope) -> int:
    """Score more specific matching scopes above global user facts."""

    if memory.task_id is not None and memory.task_id == scope.task_id:
        return 10_000
    if memory.run_id is not None and memory.run_id == scope.run_id:
        return 8_500
    if memory.session_id is not None and memory.session_id == scope.session_id:
        return 7_000
    return 5_000


async def _resolve_retrieval_replay(
    db_session: AsyncSession,
    retrieval: LlmMemoryRetrieval,
    request_hash: str,
) -> MemoryRetrievalWriteResult:
    """Return identical retrieval evidence or reject idempotency-key request drift."""

    if retrieval.request_hash != request_hash:
        raise ResourceConflictError(
            "Memory retrieval idempotency key was reused with another request"
        )
    return MemoryRetrievalWriteResult(
        retrieval=await get_memory_retrieval(
            db_session,
            retrieval.memory_retrieval_id,
        ),
        was_replayed=True,
    )


def _database_time(value: datetime) -> datetime:
    """Convert an aware UTC timestamp to the driver-neutral persisted comparison form."""

    return value.astimezone(UTC).replace(tzinfo=None)


def _aware_utc(value: datetime) -> datetime:
    """Normalize naive SQLite/MySQL timestamps and aware timestamps to UTC."""

    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _new_retrieval_id() -> str:
    """Generate a stable external identifier without importing persistence defaults indirectly."""

    from nexuspilot_api.models import new_id

    return new_id()
