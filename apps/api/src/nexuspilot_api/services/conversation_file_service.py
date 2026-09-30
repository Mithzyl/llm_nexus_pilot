"""Resolve immutable uploaded files visible at a conversation's saved message anchor."""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from nexuspilot_api.core.errors import ResourceConflictError, ResourceNotFoundError
from nexuspilot_api.models import LlmMessage, LlmRun, MessageRole, UserFile

MAX_CONVERSATION_FILES = 10


async def conversation_file_ids(
    db: AsyncSession, run_id: str, *, current_user_message_id: str | None = None
) -> list[str]:
    """Select only this owner's same-session uploads at or before the saved user turn.

    Deleted records remain references so new model calls fail explicitly rather
    than silently answer without requested evidence. Future uploads cannot change
    an earlier context's idempotency hash. Legacy artifacts are never inherited.
    """
    run = await db.get(LlmRun, run_id)
    if run is None:
        raise ResourceNotFoundError("Run")
    anchor_query = select(func.min(LlmMessage.sequence)).where(
        LlmMessage.run_id == run_id,
        LlmMessage.session_id == run.session_id,
        LlmMessage.role == MessageRole.USER,
    )
    if current_user_message_id is not None:
        anchor_query = anchor_query.where(LlmMessage.message_id == current_user_message_id)
    sequence = await db.scalar(anchor_query)
    if current_user_message_id is not None and sequence is None:
        raise ResourceConflictError("File context requires the current Run's saved User Message")
    statement = select(UserFile.file_id).where(UserFile.user_id == run.user_id)
    if run.session_id is None or sequence is None:
        statement = statement.where(UserFile.run_id == run_id)
    else:
        bound_before_anchor = (
            select(LlmMessage.message_id)
            .where(
                LlmMessage.run_id == UserFile.run_id,
                LlmMessage.session_id == run.session_id,
                LlmMessage.role == MessageRole.USER,
            LlmMessage.sequence <= sequence,
            )
            .exists()
        )
        statement = statement.join(LlmRun, LlmRun.run_id == UserFile.run_id).where(
            LlmRun.user_id == run.user_id,
            LlmRun.session_id == run.session_id,
            bound_before_anchor,
        )
    ids = list(
        (
            await db.scalars(statement.order_by(UserFile.file_id).limit(MAX_CONVERSATION_FILES + 1))
        ).all()
    )
    if len(ids) > MAX_CONVERSATION_FILES:
        raise ResourceConflictError("当前会话附件超过 10 个，请新建会话并只上传本次需要的文件。")
    return ids
