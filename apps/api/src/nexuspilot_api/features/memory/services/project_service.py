"""Minimal Project scope, Memory policy, and workspace use cases."""

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC

from sqlalchemy import and_, or_, select
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
    database_query_fingerprint,
)
from nexuspilot_api.features.memory.schemas.projects import (
    ProjectCreate,
    ProjectMemoryPolicyAction,
    ProjectMemoryPolicyUpdate,
    ProjectRead,
    ProjectUpdate,
    ProjectWorkspaceCreate,
    ProjectWorkspaceRead,
)
from nexuspilot_api.models import (
    LlmProject,
    LlmProjectWorkspace,
    ProjectMemoryStatus,
    ProjectStatus,
    User,
    new_id,
)
from nexuspilot_api.models.base import utc_now
from nexuspilot_api.schemas.pagination import CursorPage

_CREDENTIAL_URL_PATTERN = re.compile(r"://[^/@\s]+@")
_LOCAL_PATH_PATTERN = re.compile(r"^(?:/|[A-Za-z]:[\\/])")


@dataclass(frozen=True)
class ProjectWriteResult:
    """Contain one Project representation and whether an idempotent replay happened."""

    project: ProjectRead
    was_replayed: bool = False


async def create_project(
    db_session: AsyncSession,
    payload: ProjectCreate,
) -> ProjectWriteResult:
    """Create one minimal Project scope without enabling Memory automatically."""

    user = await db_session.get(User, payload.owner_user_id)
    if user is None or not user.is_active:
        raise ResourceConflictError("Project owner must exist and be active")
    project = LlmProject(
        project_id=new_id(),
        owner_user_id=payload.owner_user_id,
        project_name=payload.project_name,
        status=ProjectStatus.ACTIVE,
        memory_status=ProjectMemoryStatus.DISABLED,
    )
    db_session.add(project)
    await db_session.commit()
    await db_session.refresh(project)
    return ProjectWriteResult(project=ProjectRead.model_validate(project))


async def get_project(db_session: AsyncSession, project_id: str) -> ProjectRead:
    """Return one Project or raise the stable not-found error."""

    project = await db_session.get(LlmProject, project_id)
    if project is None:
        raise ResourceNotFoundError("Project")
    return ProjectRead.model_validate(project)


async def list_projects(
    db_session: AsyncSession,
    *,
    codec: CursorCodec,
    cursor: str | None,
    owner_user_id: str | None,
    project_status: ProjectStatus | None,
    limit: int,
) -> CursorPage[ProjectRead]:
    """Return a filter-bound, signed-cursor page of Projects."""

    query_fingerprint = database_query_fingerprint(
        "projects",
        {
            "owner_user_id": owner_user_id,
            "status": project_status.value if project_status else None,
        },
    )
    after_database_key = codec.decode_query(cursor) if cursor else None
    if after_database_key and after_database_key.query_fingerprint != query_fingerprint:
        raise InvalidCursorError
    statement = select(LlmProject)
    if owner_user_id is not None:
        statement = statement.where(LlmProject.owner_user_id == owner_user_id)
    if project_status is not None:
        statement = statement.where(LlmProject.status == project_status)
    if after_database_key is not None:
        cursor_time = after_database_key.created_at.astimezone(UTC).replace(tzinfo=None)
        statement = statement.where(
            or_(
                LlmProject.created_at > cursor_time,
                and_(
                    LlmProject.created_at == cursor_time,
                    LlmProject.project_id > after_database_key.identifier,
                ),
            )
        )
    projects = list(
        (
            await db_session.scalars(
                statement.order_by(LlmProject.created_at, LlmProject.project_id).limit(limit + 1)
            )
        ).all()
    )
    has_more = len(projects) > limit
    items = projects[:limit]
    next_cursor = None
    if has_more and items:
        last_project = items[-1]
        next_cursor = codec.encode_query(
            DatabaseQueryPaginationKey(
                created_at=last_project.created_at,
                identifier=last_project.project_id,
                query_fingerprint=query_fingerprint,
            )
        )
    return CursorPage[ProjectRead](
        items=[ProjectRead.model_validate(item) for item in items],
        next_cursor=next_cursor,
        has_more=next_cursor is not None,
        limit=limit,
    )


async def update_project(
    db_session: AsyncSession,
    project_id: str,
    payload: ProjectUpdate,
) -> ProjectRead:
    """Update declared mutable Project display fields."""

    project = await db_session.get(LlmProject, project_id)
    if project is None:
        raise ResourceNotFoundError("Project")
    if payload.project_name is not None:
        project.project_name = payload.project_name
    await db_session.commit()
    await db_session.refresh(project)
    return ProjectRead.model_validate(project)


async def update_memory_policy(
    db_session: AsyncSession,
    project_id: str,
    payload: ProjectMemoryPolicyUpdate,
) -> ProjectRead:
    """Explicitly enable, suspend, or disable Project Memory without guessing."""

    project = await db_session.scalar(
        select(LlmProject).where(LlmProject.project_id == project_id).with_for_update()
    )
    if project is None:
        raise ResourceNotFoundError("Project")
    if project.status == ProjectStatus.ARCHIVED and payload.action in {
        ProjectMemoryPolicyAction.ENABLE,
    }:
        raise ResourceConflictError("Cannot enable Memory for an archived Project")
    target_status = {
        ProjectMemoryPolicyAction.ENABLE: ProjectMemoryStatus.ENABLED,
        ProjectMemoryPolicyAction.SUSPEND: ProjectMemoryStatus.SUSPENDED,
        ProjectMemoryPolicyAction.DISABLE: ProjectMemoryStatus.DISABLED,
    }[payload.action]
    project.memory_status = target_status
    if target_status == ProjectMemoryStatus.ENABLED:
        project.memory_enabled_at = utc_now()
        project.memory_enabled_by_actor_id = payload.actor_id
    elif project.memory_status != ProjectMemoryStatus.ENABLED:
        project.memory_enabled_at = None
    await db_session.commit()
    await db_session.refresh(project)
    return ProjectRead.model_validate(project)


async def add_project_workspace(
    db_session: AsyncSession,
    project_id: str,
    payload: ProjectWorkspaceCreate,
) -> ProjectWorkspaceRead:
    """Bind one sanitized, credential-free locator to a Project."""

    project = await db_session.get(LlmProject, project_id)
    if project is None:
        raise ResourceNotFoundError("Project")
    sanitized = sanitize_workspace_locator(payload.credential_free_locator)
    locator_hash = hashlib.sha256(sanitized.encode()).hexdigest()
    workspace = LlmProjectWorkspace(
        project_workspace_id=new_id(),
        project_id=project_id,
        workspace_type=payload.workspace_type,
        credential_free_locator=sanitized,
        locator_hash=locator_hash,
        is_active=True,
    )
    db_session.add(workspace)
    await db_session.commit()
    await db_session.refresh(workspace)
    return ProjectWorkspaceRead.model_validate(workspace)


def sanitize_workspace_locator(locator: str) -> str:
    """Strip credentials and reject local paths that would leak machine state."""

    stripped = _CREDENTIAL_URL_PATTERN.sub("://", locator)
    if _LOCAL_PATH_PATTERN.match(stripped):
        raise InvalidRequestError("Workspace locator must not be a local filesystem path")
    if stripped != locator or not stripped.strip():
        raise InvalidRequestError("Workspace locator must be credential-free")
    return stripped
