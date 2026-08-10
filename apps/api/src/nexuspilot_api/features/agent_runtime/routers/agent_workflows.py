"""Agent workflow creation, complete result, node, and replayable SSE controllers."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import suppress
from typing import Annotated

from fastapi import APIRouter, Header, Query, Response, status
from fastapi.responses import StreamingResponse

from nexuspilot_api.core.errors import InvalidRequestError
from nexuspilot_api.features.agent_runtime.dependencies import (
    AgentWorkflowExecutionServiceDependency,
)
from nexuspilot_api.features.agent_runtime.schemas.agent_workflows import (
    AgentWorkflowCreate,
    AgentWorkflowNodeResultRead,
    AgentWorkflowResultRead,
    AgentWorkflowSummaryRead,
)
from nexuspilot_api.features.agent_runtime.services.workflow_query_service import (
    get_run_workflow_summary,
    get_workflow_node,
    get_workflow_result,
    get_workflow_summary,
    list_workflow_events,
    list_workflow_nodes,
)
from nexuspilot_api.routers.common import CursorCodecDependency, DatabaseSessionDependency
from nexuspilot_api.schemas.pagination import CursorPage

router = APIRouter(tags=["agent-workflows"])

WORKFLOW_RESULT_CONTENT = {
    "application/json": {"schema": {"$ref": "#/components/schemas/AgentWorkflowResultRead"}},
    "text/event-stream": {"schema": {"type": "string"}},
}


@router.post(
    "/runs/{run_id}/agent-workflows",
    response_model=AgentWorkflowResultRead,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_200_OK: {
            "description": "Exact idempotent JSON result or persisted SSE replay",
            "content": WORKFLOW_RESULT_CONTENT,
        },
        status.HTTP_201_CREATED: {
            "description": "New synchronous JSON result or live committed SSE stream",
            "content": WORKFLOW_RESULT_CONTENT,
        },
    },
)
async def post_agent_workflow(
    run_id: str,
    payload: AgentWorkflowCreate,
    response: Response,
    service: AgentWorkflowExecutionServiceDependency,
) -> AgentWorkflowResultRead | StreamingResponse:
    """Execute one idempotent model-only workflow and return JSON or persisted SSE events."""

    if payload.stream:
        event_queue: asyncio.Queue = asyncio.Queue()
        workflow, replayed = await service.prepare_workflow(
            run_id,
            payload,
            event_sink=event_queue.put,
        )
        if replayed:
            events = await list_workflow_events(
                service.db_session,
                workflow.workflow_execution_id,
                after_sequence=0,
            )

            async def replay_source() -> AsyncIterator[str]:
                """Replay the complete durable event history for an exact idempotent request."""

                for event in events:
                    yield event.to_sse()

            return StreamingResponse(
                replay_source(),
                status_code=status.HTTP_200_OK,
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )

        async def live_event_source() -> AsyncIterator[str]:
            """Execute the prepared workflow while forwarding only committed events."""

            execution_task = asyncio.create_task(
                service.execute_prepared_workflow_in_new_session(
                    workflow.workflow_execution_id,
                    payload,
                    event_sink=event_queue.put,
                )
            )
            try:
                while not execution_task.done() or not event_queue.empty():
                    try:
                        event = await asyncio.wait_for(event_queue.get(), timeout=0.1)
                    except TimeoutError:
                        continue
                    yield event.to_sse()
                await execution_task
            finally:
                if not execution_task.done():
                    execution_task.cancel()
                with suppress(asyncio.CancelledError):
                    await execution_task

        return StreamingResponse(
            live_event_source(),
            status_code=status.HTTP_201_CREATED,
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    result, replayed = await service.create_and_execute(run_id, payload)
    if replayed:
        response.status_code = status.HTTP_200_OK
    return result


@router.get(
    "/runs/{run_id}/agent-workflow",
    response_model=AgentWorkflowSummaryRead,
)
async def get_run_agent_workflow(
    run_id: str,
    db_session: DatabaseSessionDependency,
) -> AgentWorkflowSummaryRead:
    """Discover the single Agent workflow associated with a durable Run."""

    return await get_run_workflow_summary(db_session, run_id)


@router.get(
    "/agent-workflows/{workflow_execution_id}",
    response_model=AgentWorkflowSummaryRead,
)
async def get_agent_workflow(
    workflow_execution_id: str,
    db_session: DatabaseSessionDependency,
) -> AgentWorkflowSummaryRead:
    """Return the current stage and every active node identity for one workflow."""

    return await get_workflow_summary(db_session, workflow_execution_id)


@router.get(
    "/agent-workflows/{workflow_execution_id}/result",
    response_model=AgentWorkflowResultRead,
)
async def get_agent_workflow_result(
    workflow_execution_id: str,
    db_session: DatabaseSessionDependency,
) -> AgentWorkflowResultRead:
    """Return one snapshot containing all committed complete node parameters."""

    return await get_workflow_result(db_session, workflow_execution_id)


@router.get(
    "/agent-workflows/{workflow_execution_id}/nodes",
    response_model=CursorPage[AgentWorkflowNodeResultRead],
)
async def get_agent_workflow_nodes(
    workflow_execution_id: str,
    db_session: DatabaseSessionDependency,
    cursor_codec: CursorCodecDependency,
    cursor: Annotated[str | None, Query(max_length=2_048)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> CursorPage[AgentWorkflowNodeResultRead]:
    """Page complete node results in durable execution-sequence order."""

    return await list_workflow_nodes(
        db_session,
        workflow_execution_id,
        codec=cursor_codec,
        cursor=cursor,
        limit=limit,
    )


@router.get(
    "/agent-workflows/{workflow_execution_id}/nodes/{node_execution_id}",
    response_model=AgentWorkflowNodeResultRead,
)
async def get_agent_workflow_node(
    workflow_execution_id: str,
    node_execution_id: str,
    db_session: DatabaseSessionDependency,
) -> AgentWorkflowNodeResultRead:
    """Return one complete node result only within its owning workflow."""

    return await get_workflow_node(db_session, workflow_execution_id, node_execution_id)


@router.get(
    "/agent-workflows/{workflow_execution_id}/events",
    response_model=None,
    response_class=StreamingResponse,
    responses={
        status.HTTP_200_OK: {
            "description": "Finite replay of already-persisted workflow events",
            "content": {"text/event-stream": {"schema": {"type": "string"}}},
        }
    },
)
async def get_agent_workflow_events(
    workflow_execution_id: str,
    db_session: DatabaseSessionDependency,
    after_sequence: Annotated[int | None, Query(ge=0)] = None,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> StreamingResponse:
    """Replay ordered workflow business events after a query or SSE acknowledgment."""

    acknowledged_sequence = after_sequence
    if acknowledged_sequence is None and last_event_id is not None:
        try:
            acknowledged_sequence = int(last_event_id)
        except ValueError as exc:
            raise InvalidRequestError("Last-Event-ID must be a non-negative integer") from exc
        if acknowledged_sequence < 0:
            raise InvalidRequestError("Last-Event-ID must be a non-negative integer")
    events = await list_workflow_events(
        db_session,
        workflow_execution_id,
        after_sequence=acknowledged_sequence or 0,
    )

    async def event_source() -> AsyncIterator[str]:
        """Serialize already-persisted events without holding a database transaction."""

        for event in events:
            yield event.to_sse()

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
