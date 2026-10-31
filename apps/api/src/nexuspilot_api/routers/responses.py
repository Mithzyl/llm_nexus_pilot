"""Provider-neutral model Responses controller."""

from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from nexuspilot_api.core.dependencies import ModelInvocationServiceDependency
from nexuspilot_api.infrastructure.database import get_database_session_factory
from nexuspilot_api.schemas.responses import ResponsesRequest, ResponsesResult
from nexuspilot_api.services.response_run_lifecycle import ConversationResponseService

router = APIRouter(tags=["responses"])


@router.post("/responses", response_model=None)
async def post_response(
    payload: ResponsesRequest,
    service: ModelInvocationServiceDependency,
    factory: Annotated[Any, Depends(get_database_session_factory)] = None,
) -> ResponsesResult | StreamingResponse:
    """Execute whole conversation turns with durable terminal state and automatic file tools."""

    if (
        getattr(getattr(service, "settings", None), "workflow_execution_backend", None) == "worker"
        and payload.stream
        and payload.current_user_message_id
        and payload.idempotency_key
        and not payload.task_id
        and not payload.tools
    ):
        from nexuspilot_api.services.quick_response_observer import observe_quick_response
        from nexuspilot_api.services.quick_response_submission import accept_quick_response

        owner = service.db_session.info.get("authorized_user_id")
        accepted = await accept_quick_response(service.db_session, service.storage, owner, payload)

        async def durable_events():
            """Deliver saved events; closing this HTTP iterator never stops the Worker."""
            async for event in observe_quick_response(
                factory, service.storage, owner, accepted.response_execution_id
            ):
                yield (f"id: {event.sequence}\nevent: {event.type.value}\n"
                       f"data: {event.model_dump_json()}\n\n")

        return StreamingResponse(
            durable_events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    service = ConversationResponseService(service)
    if not payload.stream:
        return await service.generate(payload)

    async def event_source() -> AsyncIterator[str]:
        """Serialize provider-neutral stream events using the SSE wire format."""

        async for event in service.stream(payload):
            data = event.model_dump_json()
            yield (f"id: {event.sequence}\nevent: {event.type.value}\ndata: {data}\n\n")

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
