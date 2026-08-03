"""User and Project core Profile snapshot generation with pointer switching."""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from nexuspilot_api.core.errors import ResourceConflictError, ResourceNotFoundError
from nexuspilot_api.features.memory.schemas.projects import ProjectMemoryProfileRead
from nexuspilot_api.features.memory.schemas.user_memory import UserMemoryProfileRead
from nexuspilot_api.features.memory.services.memory_policy import hash_memory_request
from nexuspilot_api.features.memory.services.profile_builder import (
    build_project_profile,
    build_user_profile,
)
from nexuspilot_api.features.memory.services.snapshot_protocol import (
    upload_and_register_snapshot,
)
from nexuspilot_api.infrastructure.object_storage import ObjectStorage
from nexuspilot_api.models import (
    LlmProject,
    LlmProjectMemoryProfileItem,
    LlmProjectMemoryProfileSnapshot,
    LlmUserMemoryProfileItem,
    LlmUserMemoryProfileSnapshot,
    SessionMemoryStatus,
    new_id,
)
from nexuspilot_api.models.base import utc_now


@dataclass(frozen=True)
class ProfileSnapshotWriteResult:
    """Contain one activated Profile snapshot version and whether it replayed."""

    record: ProjectMemoryProfileRead | UserMemoryProfileRead
    was_replayed: bool


async def rebuild_project_profile(
    db_session: AsyncSession,
    storage: ObjectStorage,
    project_id: str,
    *,
    expected_previous_version: int,
    tokenizer_name: str,
    tokenizer_version: str,
    idempotency_key: str,
) -> ProfileSnapshotWriteResult:
    """Rebuild one Project core Profile from current formal facts atomically."""

    project = await db_session.scalar(
        select(LlmProject).where(LlmProject.project_id == project_id).with_for_update()
    )
    if project is None:
        raise ResourceNotFoundError("Project")
    request_hash = hash_memory_request(
        {
            "project_id": project_id,
            "expected_previous_version": expected_previous_version,
            "tokenizer_name": tokenizer_name,
            "tokenizer_version": tokenizer_version,
            "idempotency_key": idempotency_key,
        }
    )
    replay = await db_session.scalar(
        select(LlmProjectMemoryProfileSnapshot).where(
            LlmProjectMemoryProfileSnapshot.project_id == project_id,
            LlmProjectMemoryProfileSnapshot.idempotency_key == idempotency_key,
        )
    )
    if replay is not None:
        if replay.request_hash != request_hash:
            raise ResourceConflictError(
                "Project profile idempotency key was reused with another request"
            )
        return ProfileSnapshotWriteResult(
            record=ProjectMemoryProfileRead.model_validate(replay),
            was_replayed=True,
        )
    built = await build_project_profile(
        db_session,
        project_id=project_id,
        user_id=project.owner_user_id,
    )
    current_id = project.current_memory_profile_snapshot_id
    current_version = 0
    if current_id is not None:
        current_snapshot = await db_session.get(LlmProjectMemoryProfileSnapshot, current_id)
        if current_snapshot is None:
            raise ResourceConflictError("Project profile pointer is unavailable")
        current_version = current_snapshot.version
    if expected_previous_version != current_version:
        raise ResourceConflictError(
            "Project profile version does not match expected_previous_version"
        )
    new_version = current_version + 1
    snapshot_id = new_id()
    try:
        registered = await upload_and_register_snapshot(
            db_session=db_session,
            storage=storage,
            user_id=project.owner_user_id,
            memory_layer="l3",
            scope_id=project_id,
            object_type="project_profile",
            schema_version="project_profile.v1",
            version=new_version,
            object_id=snapshot_id,
            content=built.profile_json,
            source_ids=[selection.memory_id for selection in built.selections],
            project_id=project_id,
            created_at=utc_now(),
        )
        if current_snapshot_id := current_id:
            current_snapshot = await db_session.get(
                LlmProjectMemoryProfileSnapshot, current_snapshot_id
            )
            if current_snapshot is not None:
                current_snapshot.status = SessionMemoryStatus.SUPERSEDED
        snapshot = LlmProjectMemoryProfileSnapshot(
            project_memory_profile_snapshot_id=snapshot_id,
            project_id=project_id,
            schema_version="project_profile.v1",
            version=new_version,
            status=SessionMemoryStatus.ACTIVE,
            profile_json=built.profile_json,
            estimated_token_count=built.estimated_token_count,
            tokenizer_name=tokenizer_name,
            tokenizer_version=tokenizer_version,
            previous_snapshot_id=current_id,
            json_snapshot_object_id=registered.json_object_id,
            markdown_snapshot_object_id=registered.markdown_object_id,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            activated_at=utc_now(),
        )
        db_session.add(snapshot)
        # Persist the referenced immutable snapshot before its item rows and
        # current pointer so strict foreign-key engines never observe a gap.
        await db_session.flush()
        db_session.add_all(
            [
                LlmProjectMemoryProfileItem(
                    project_memory_profile_snapshot_id=snapshot_id,
                    memory_id=selection.memory_id,
                    memory_version_id=selection.memory_version_id,
                    field_path=selection.field_path,
                    item_order=selection.order,
                    content_hash=selection.content_hash,
                )
                for selection in built.selections
            ]
        )
        project.current_memory_profile_snapshot_id = snapshot_id
        await db_session.commit()
    except (IntegrityError, OperationalError) as exc:
        await db_session.rollback()
        replay = await db_session.scalar(
            select(LlmProjectMemoryProfileSnapshot).where(
                LlmProjectMemoryProfileSnapshot.project_id == project_id,
                LlmProjectMemoryProfileSnapshot.idempotency_key == idempotency_key,
            )
        )
        if replay is not None:
            return ProfileSnapshotWriteResult(
                record=ProjectMemoryProfileRead.model_validate(replay),
                was_replayed=True,
            )
        raise ResourceConflictError("Project profile rebuild conflicted") from exc
    return ProfileSnapshotWriteResult(
        record=ProjectMemoryProfileRead.model_validate(snapshot),
        was_replayed=False,
    )


async def rebuild_user_profile(
    db_session: AsyncSession,
    storage: ObjectStorage,
    user_id: str,
    *,
    expected_previous_version: int,
    tokenizer_name: str,
    tokenizer_version: str,
    idempotency_key: str,
) -> ProfileSnapshotWriteResult:
    """Rebuild one User core Profile from current approved formal facts atomically."""

    from nexuspilot_api.models import User

    user = await db_session.scalar(select(User).where(User.user_id == user_id).with_for_update())
    if user is None:
        raise ResourceNotFoundError("User")
    request_hash = hash_memory_request(
        {
            "user_id": user_id,
            "expected_previous_version": expected_previous_version,
            "tokenizer_name": tokenizer_name,
            "tokenizer_version": tokenizer_version,
            "idempotency_key": idempotency_key,
        }
    )
    replay = await db_session.scalar(
        select(LlmUserMemoryProfileSnapshot).where(
            LlmUserMemoryProfileSnapshot.user_id == user_id,
            LlmUserMemoryProfileSnapshot.idempotency_key == idempotency_key,
        )
    )
    if replay is not None:
        if replay.request_hash != request_hash:
            raise ResourceConflictError(
                "User profile idempotency key was reused with another request"
            )
        return ProfileSnapshotWriteResult(
            record=UserMemoryProfileRead.model_validate(replay),
            was_replayed=True,
        )
    built = await build_user_profile(db_session, user_id=user_id)
    current_id = user.current_memory_profile_snapshot_id
    current_version = 0
    if current_id is not None:
        current_snapshot = await db_session.get(LlmUserMemoryProfileSnapshot, current_id)
        if current_snapshot is None:
            raise ResourceConflictError("User profile pointer is unavailable")
        current_version = current_snapshot.version
    if expected_previous_version != current_version:
        raise ResourceConflictError("User profile version does not match expected_previous_version")
    new_version = current_version + 1
    snapshot_id = new_id()
    try:
        registered = await upload_and_register_snapshot(
            db_session=db_session,
            storage=storage,
            user_id=user_id,
            memory_layer="l4",
            scope_id=user_id,
            object_type="user_profile",
            schema_version="user_profile.v1",
            version=new_version,
            object_id=snapshot_id,
            content=built.profile_json,
            source_ids=[selection.memory_id for selection in built.selections],
            created_at=utc_now(),
        )
        if current_snapshot_id := current_id:
            current_snapshot = await db_session.get(
                LlmUserMemoryProfileSnapshot, current_snapshot_id
            )
            if current_snapshot is not None:
                current_snapshot.status = SessionMemoryStatus.SUPERSEDED
        snapshot = LlmUserMemoryProfileSnapshot(
            user_memory_profile_snapshot_id=snapshot_id,
            user_id=user_id,
            schema_version="user_profile.v1",
            version=new_version,
            status=SessionMemoryStatus.ACTIVE,
            profile_json=built.profile_json,
            estimated_token_count=built.estimated_token_count,
            tokenizer_name=tokenizer_name,
            tokenizer_version=tokenizer_version,
            previous_snapshot_id=current_id,
            json_snapshot_object_id=registered.json_object_id,
            markdown_snapshot_object_id=registered.markdown_object_id,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            activated_at=utc_now(),
        )
        db_session.add(snapshot)
        # The user row has a foreign key to the snapshot, so insert the
        # immutable target before changing the user's current pointer.
        await db_session.flush()
        db_session.add_all(
            [
                LlmUserMemoryProfileItem(
                    user_memory_profile_snapshot_id=snapshot_id,
                    memory_id=selection.memory_id,
                    memory_version_id=selection.memory_version_id,
                    field_path=selection.field_path,
                    item_order=selection.order,
                    content_hash=selection.content_hash,
                )
                for selection in built.selections
            ]
        )
        user.current_memory_profile_snapshot_id = snapshot_id
        await db_session.commit()
    except (IntegrityError, OperationalError) as exc:
        await db_session.rollback()
        replay = await db_session.scalar(
            select(LlmUserMemoryProfileSnapshot).where(
                LlmUserMemoryProfileSnapshot.user_id == user_id,
                LlmUserMemoryProfileSnapshot.idempotency_key == idempotency_key,
            )
        )
        if replay is not None:
            return ProfileSnapshotWriteResult(
                record=UserMemoryProfileRead.model_validate(replay),
                was_replayed=True,
            )
        raise ResourceConflictError("User profile rebuild conflicted") from exc
    return ProfileSnapshotWriteResult(
        record=UserMemoryProfileRead.model_validate(snapshot),
        was_replayed=False,
    )


async def get_user_profile(
    db_session: AsyncSession,
    user_id: str,
) -> UserMemoryProfileRead:
    """Return the current User core Profile snapshot."""

    from nexuspilot_api.models import User

    user = await db_session.get(User, user_id)
    if user is None:
        raise ResourceNotFoundError("User")
    if user.current_memory_profile_snapshot_id is None:
        raise ResourceNotFoundError("User Memory Profile")
    snapshot = await db_session.get(
        LlmUserMemoryProfileSnapshot, user.current_memory_profile_snapshot_id
    )
    if snapshot is None:
        raise ResourceConflictError("User profile pointer is unavailable")
    return UserMemoryProfileRead.model_validate(snapshot)


async def get_project_profile(
    db_session: AsyncSession,
    project_id: str,
) -> ProjectMemoryProfileRead:
    """Return the current Project core Profile snapshot."""

    project = await db_session.get(LlmProject, project_id)
    if project is None:
        raise ResourceNotFoundError("Project")
    if project.current_memory_profile_snapshot_id is None:
        raise ResourceNotFoundError("Project Memory Profile")
    snapshot = await db_session.get(
        LlmProjectMemoryProfileSnapshot, project.current_memory_profile_snapshot_id
    )
    if snapshot is None:
        raise ResourceConflictError("Project profile pointer is unavailable")
    return ProjectMemoryProfileRead.model_validate(snapshot)
