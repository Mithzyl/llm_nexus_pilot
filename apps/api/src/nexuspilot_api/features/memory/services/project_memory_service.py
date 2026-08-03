"""L3 Project Memory candidate review use cases."""

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from nexuspilot_api.core.errors import (
    InvalidCursorError,
    ResourceConflictError,
    ResourceNotFoundError,
)
from nexuspilot_api.core.pagination import (
    CursorCodec,
    DatabaseQueryPaginationKey,
    database_query_fingerprint,
)
from nexuspilot_api.features.memory.schemas.memories import MemoryRead, MemoryUpdate
from nexuspilot_api.features.memory.schemas.projects import ProjectMemoryCandidateRead
from nexuspilot_api.features.memory.schemas.user_memory import CandidateDecisionCreate
from nexuspilot_api.features.memory.services.memory_policy import PROJECT_MEMORY_TYPES
from nexuspilot_api.features.memory.services.memory_service import update_memory
from nexuspilot_api.models import (
    LlmMemory,
    LlmMemoryVersion,
    LlmProject,
    MemoryStatus,
)
from nexuspilot_api.schemas.pagination import CursorPage


async def list_project_memory_candidates(
    db_session: AsyncSession,
    project_id: str,
    *,
    codec: CursorCodec,
    cursor: str | None,
    status: MemoryStatus | None,
    limit: int,
) -> CursorPage[ProjectMemoryCandidateRead]:
    """Return a filter-bound page of Project Memory candidates and formal facts."""

    project = await db_session.get(LlmProject, project_id)
    if project is None:
        raise ResourceNotFoundError("Project")
    query_fingerprint = database_query_fingerprint(
        "project-memory-candidates",
        {
            "project_id": project_id,
            "status": status.value if status else None,
        },
    )
    after_database_key = codec.decode_query(cursor) if cursor else None
    if after_database_key and after_database_key.query_fingerprint != query_fingerprint:
        raise InvalidCursorError
    statement = (
        select(LlmMemory, LlmMemoryVersion)
        .join(
            LlmMemoryVersion,
            and_(
                LlmMemoryVersion.memory_id == LlmMemory.memory_id,
                LlmMemoryVersion.version_number == LlmMemory.current_version_number,
            ),
        )
        .where(
            LlmMemory.project_id == project_id,
            LlmMemory.memory_type.in_(PROJECT_MEMORY_TYPES),
        )
    )
    if status is not None:
        statement = statement.where(LlmMemory.status == status)
    else:
        statement = statement.where(LlmMemory.status != MemoryStatus.DELETED)
    if after_database_key is not None:
        cursor_time = after_database_key.created_at.astimezone().replace(tzinfo=None)
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
    items = rows[:limit]
    next_cursor = None
    if has_more and items:
        last_memory = items[-1][0]
        next_cursor = codec.encode_query(
            DatabaseQueryPaginationKey(
                created_at=last_memory.created_at,
                identifier=last_memory.memory_id,
                query_fingerprint=query_fingerprint,
            )
        )
    return CursorPage[ProjectMemoryCandidateRead](
        items=[_candidate_read(memory, version) for memory, version in items],
        next_cursor=next_cursor,
        has_more=next_cursor is not None,
        limit=limit,
    )


async def approve_project_memory_candidate(
    db_session: AsyncSession,
    project_id: str,
    memory_id: str,
    payload: CandidateDecisionCreate,
) -> MemoryRead:
    """Activate one Project candidate without coupling approval to snapshot storage."""

    project = await db_session.get(LlmProject, project_id)
    if project is None:
        raise ResourceNotFoundError("Project")
    memory = await db_session.get(LlmMemory, memory_id)
    if memory is None:
        raise ResourceNotFoundError("Memory")
    if memory.project_id != project_id:
        raise ResourceConflictError("Memory does not belong to the Project")
    if memory.status != MemoryStatus.CANDIDATE:
        raise ResourceConflictError("Memory is not a Project candidate")
    result = await update_memory(
        db_session,
        memory_id,
        MemoryUpdate(
            expected_version_number=payload.expected_version_number,
            idempotency_key=payload.idempotency_key,
            status=MemoryStatus.ACTIVE,
        ),
    )
    return result.memory


def _candidate_read(
    memory: LlmMemory,
    version: LlmMemoryVersion,
) -> ProjectMemoryCandidateRead:
    """Map one Project Memory row to the bounded candidate response."""

    return ProjectMemoryCandidateRead(
        memory_id=memory.memory_id,
        memory_type=memory.memory_type.value,
        status=memory.status,
        version_number=version.version_number,
        content_text=version.content_text,
        semantic_key=memory.semantic_key,
        importance=str(version.importance),
        confidence=str(version.confidence),
        approval_method=memory.approval_method.value,
        approved_at=memory.approved_at,
        sensitivity_classification=memory.sensitivity_classification.value,
        expires_at=memory.expires_at,
        created_at=memory.created_at,
        updated_at=memory.updated_at,
    )
