"""Project, persist, and query public model reasoning state and replay events."""

from datetime import UTC, datetime

from fastapi import HTTPException, status
from nexuspilot_models.contracts import (
    ModelReasoningCapabilities,
    ReasoningDisplayPolicy,
    ReasoningPresentation,
    ReasoningPresentationCapability,
    ReasoningPresentationKind,
    StreamEvent,
    StreamEventType,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from nexuspilot_api.models import (
    LlmModelAttempt,
    LlmModelResponseEvent,
    LlmReasoningBlock,
)
from nexuspilot_api.schemas.model_reasoning import (
    ModelResponseEventPage,
    ModelResponseEventRead,
    ReasoningBlockRead,
)


def project_reasoning_presentation(
    presentation: ReasoningPresentation,
    *,
    capabilities: ModelReasoningCapabilities,
    display_policy: ReasoningDisplayPolicy,
) -> ReasoningPresentation | None:
    """Clamp one provider candidate to model capability and server-selected disclosure policy."""

    if capabilities.presentation is ReasoningPresentationCapability.NONE:
        return None
    if presentation.kind is ReasoningPresentationKind.STATUS:
        return presentation
    if display_policy is ReasoningDisplayPolicy.HIDDEN:
        return presentation.model_copy(
            update={"kind": ReasoningPresentationKind.STATUS, "text": None}
        )
    if presentation.kind is ReasoningPresentationKind.RAW:
        if (
            capabilities.presentation is ReasoningPresentationCapability.RAW
            and display_policy is ReasoningDisplayPolicy.PROVIDER_VISIBLE
        ):
            return presentation
        return presentation.model_copy(
            update={"kind": ReasoningPresentationKind.STATUS, "text": None}
        )
    if presentation.kind is ReasoningPresentationKind.SUMMARY:
        if capabilities.presentation in {
            ReasoningPresentationCapability.RAW,
            ReasoningPresentationCapability.SUMMARY,
        }:
            return presentation
        return presentation.model_copy(
            update={"kind": ReasoningPresentationKind.STATUS, "text": None}
        )
    return None


def project_reasoning_stream_event(
    event: StreamEvent,
    *,
    capabilities: ModelReasoningCapabilities,
    display_policy: ReasoningDisplayPolicy,
) -> StreamEvent | None:
    """Remove disallowed deltas and downgrade visible lifecycle events to status-only data."""

    if capabilities.presentation is ReasoningPresentationCapability.NONE:
        return None
    if event.type is StreamEventType.REASONING_STARTED:
        requested_kind = event.data.get("kind")
        visible_kind = _project_started_kind(
            requested_kind,
            capabilities=capabilities,
            display_policy=display_policy,
        )
        return event.model_copy(update={"data": {**event.data, "kind": visible_kind}})
    if event.type is StreamEventType.REASONING_RAW_DELTA:
        if (
            capabilities.presentation is ReasoningPresentationCapability.RAW
            and display_policy is ReasoningDisplayPolicy.PROVIDER_VISIBLE
        ):
            return event
        return None
    if event.type is StreamEventType.REASONING_SUMMARY_DELTA:
        if (
            capabilities.presentation
            in {ReasoningPresentationCapability.RAW, ReasoningPresentationCapability.SUMMARY}
            and display_policy is not ReasoningDisplayPolicy.HIDDEN
        ):
            return event
        return None
    if event.type in {
        StreamEventType.REASONING_COMPLETED,
        StreamEventType.REASONING_INTERRUPTED,
    }:
        block_data = event.data.get("block")
        if not isinstance(block_data, dict):
            return None
        projected = project_reasoning_presentation(
            ReasoningPresentation.model_validate(block_data),
            capabilities=capabilities,
            display_policy=display_policy,
        )
        if projected is None:
            return None
        return event.model_copy(
            update={"data": {"block": projected.model_dump(mode="json")}}
        )
    return event


def _project_started_kind(
    requested_kind: object,
    *,
    capabilities: ModelReasoningCapabilities,
    display_policy: ReasoningDisplayPolicy,
) -> str:
    """Return the truthful public kind for a started lifecycle event."""

    if (
        requested_kind == ReasoningPresentationKind.RAW.value
        and capabilities.presentation is ReasoningPresentationCapability.RAW
        and display_policy is ReasoningDisplayPolicy.PROVIDER_VISIBLE
    ):
        return ReasoningPresentationKind.RAW.value
    if (
        requested_kind == ReasoningPresentationKind.SUMMARY.value
        and capabilities.presentation
        in {ReasoningPresentationCapability.RAW, ReasoningPresentationCapability.SUMMARY}
        and display_policy is not ReasoningDisplayPolicy.HIDDEN
    ):
        return ReasoningPresentationKind.SUMMARY.value
    return ReasoningPresentationKind.STATUS.value


async def record_model_response_event(
    db_session: AsyncSession,
    *,
    attempt_id: str,
    event: StreamEvent,
) -> None:
    """Commit one sanitized event before it is published to the browser."""

    block_id = event.data.get("block_id")
    if block_id is None and isinstance(event.data.get("block"), dict):
        block_id = event.data["block"].get("block_id")
    db_session.add(
        LlmModelResponseEvent(
            event_id=event.event_id,
            attempt_id=attempt_id,
            event_sequence=event.sequence,
            event_type=event.type.value,
            reasoning_block_id=block_id if isinstance(block_id, str) else None,
            public_payload_json=event.model_dump(mode="json"),
            schema_version=event.schema_version,
            occurred_at=datetime.fromtimestamp((event.timestamp_ms or 0) / 1000, tz=UTC),
        )
    )
    await db_session.commit()


async def get_reasoning_blocks(
    db_session: AsyncSession,
    attempt_id: str,
) -> list[ReasoningBlockRead]:
    """Return authoritative reasoning blocks after confirming the model invocation exists."""

    if await db_session.get(LlmModelAttempt, attempt_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Attempt not found")
    blocks = list(
        await db_session.scalars(
            select(LlmReasoningBlock)
            .where(LlmReasoningBlock.attempt_id == attempt_id)
            .order_by(LlmReasoningBlock.block_index)
        )
    )
    return [
        ReasoningBlockRead(
            block_id=block.reasoning_block_id,
            response_id=block.attempt_id,
            kind=ReasoningPresentationKind(block.presentation_kind),
            status=block.status,
            text=block.visible_text,
            reasoning_tokens=block.reasoning_tokens,
            started_at=block.started_at,
            first_visible_token_at=block.first_visible_token_at,
            completed_at=block.completed_at,
            final_event_sequence=block.final_event_sequence,
        )
        for block in blocks
    ]


async def list_model_response_events(
    db_session: AsyncSession,
    attempt_id: str,
    *,
    after_sequence: int,
    limit: int,
) -> ModelResponseEventPage:
    """Return committed response events in sequence order using a bounded replay page."""

    if await db_session.get(LlmModelAttempt, attempt_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Attempt not found")
    records = list(
        await db_session.scalars(
            select(LlmModelResponseEvent)
            .where(
                LlmModelResponseEvent.attempt_id == attempt_id,
                LlmModelResponseEvent.event_sequence > after_sequence,
            )
            .order_by(LlmModelResponseEvent.event_sequence)
            .limit(limit + 1)
        )
    )
    has_more = len(records) > limit
    page_records = records[:limit]
    items = [
        ModelResponseEventRead.model_validate(record.public_payload_json)
        for record in page_records
    ]
    return ModelResponseEventPage(
        items=items,
        next_after_sequence=(items[-1].sequence if items and has_more else None),
        has_more=has_more,
        limit=limit,
    )


async def count_reasoning_blocks(db_session: AsyncSession, attempt_id: str) -> int:
    """Return a bounded-summary count for model attempt detail responses."""

    return int(
        await db_session.scalar(
            select(func.count())
            .select_from(LlmReasoningBlock)
            .where(LlmReasoningBlock.attempt_id == attempt_id)
        )
        or 0
    )
