"""Provider-neutral model Responses controller."""

from collections.abc import AsyncIterator

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from nexuspilot_api.core.dependencies import ModelInvocationServiceDependency
from nexuspilot_api.schemas.responses import ResponsesRequest, ResponsesResult
from nexuspilot_api.services.response_file_tool_service import (
    ResponseFileToolService,
    has_automatic_file_tools,
)

router = APIRouter(tags=["responses"])


@router.post("/responses", response_model=None)
async def post_response(
    payload: ResponsesRequest,
    service: ModelInvocationServiceDependency,
) -> ResponsesResult | StreamingResponse:
    """Automatically offer owned conversation file tools in both JSON and streaming replies."""

    if await has_automatic_file_tools(service, payload):
        service = ResponseFileToolService(service)
    if not payload.stream:
        return await service.generate(payload)

    async def event_source() -> AsyncIterator[str]:
        """Serialize provider-neutral stream events using the SSE wire format."""

        async for event in service.stream(payload):
            data = event.model_dump_json()
            yield (
                f"id: {event.sequence}\n"
                f"event: {event.type.value}\n"
                f"data: {data}\n\n"
            )

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
