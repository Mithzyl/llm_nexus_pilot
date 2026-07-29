"""Provider-neutral model Responses controller."""

from collections.abc import AsyncIterator

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from nexuspilot_api.core.dependencies import ModelInvocationServiceDependency
from nexuspilot_api.schemas.responses import ResponsesRequest, ResponsesResult

router = APIRouter(tags=["responses"])


@router.post("/responses", response_model=None)
async def post_response(
    payload: ResponsesRequest,
    service: ModelInvocationServiceDependency,
) -> ResponsesResult | StreamingResponse:
    """Create a provider-routed response or return normalized SSE when stream is true."""

    if not payload.stream:
        return await service.generate(payload)

    async def event_source() -> AsyncIterator[str]:
        """Serialize provider-neutral stream events using the SSE wire format."""

        async for event in service.stream(payload):
            data = event.model_dump_json()
            yield f"event: {event.type.value}\ndata: {data}\n\n"

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
