"""Own Run state for saved quick-response turns, outside individual model/tool rounds."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import aclosing

from anyio import CancelScope
from nexuspilot_models.contracts import StreamEvent, StreamEventType
from sqlalchemy import exists, select, update

from nexuspilot_api.core.errors import InvalidRequestError, ResourceConflictError
from nexuspilot_api.models import LlmAgentWorkflowExecution, LlmMessage, LlmRun, LlmTask
from nexuspilot_api.models.base import utc_now
from nexuspilot_api.models.enums import MessageRole, RunStatus
from nexuspilot_api.schemas.responses import ResponsesRequest, ResponsesResult
from nexuspilot_api.services.model_response_service import ModelInvocationService
from nexuspilot_api.services.response_file_tool_service import (
    ResponseFileToolService,
    has_automatic_file_tools,
)


class ConversationResponseService:
    """Finalize only owned chat turns; leave generic calls and Agent Runs to their owners."""

    def __init__(self, invocation: ModelInvocationService) -> None:
        """Reuse the request's authorized invocation and transaction dependencies."""
        self.invocation = invocation
        self.db = invocation.db_session
        self.run_id: str | None = None

    async def _start(self, payload: ResponsesRequest) -> None:
        """Atomically claim a saved chat Run; reject duplicates before model dispatch."""
        if not payload.current_user_message_id or payload.task_id or payload.tools:
            return
        valid = await self.db.scalar(
            select(LlmMessage.message_id)
            .join(
                LlmRun,
                LlmMessage.run_id == LlmRun.run_id,
            )
            .where(
                LlmRun.run_id == payload.run_id,
                LlmMessage.message_id == payload.current_user_message_id,
                LlmMessage.session_id == LlmRun.session_id,
                LlmMessage.role == MessageRole.USER,
            )
        )
        if not valid:
            raise InvalidRequestError("Conversation message does not belong to this Run.")
        claimed = await self.db.execute(
            update(LlmRun)
            .where(
                LlmRun.run_id == payload.run_id,
                LlmRun.status == RunStatus.PENDING,
                ~exists().where(LlmAgentWorkflowExecution.run_id == LlmRun.run_id),
                ~exists().where(LlmTask.run_id == LlmRun.run_id),
            )
            .values(status=RunStatus.RUNNING, started_at=utc_now())
        )
        if claimed.rowcount != 1:
            await self.db.rollback()
            raise ResourceConflictError("Run is already executing, ended, or owned by a workflow.")
        await self.db.commit()
        self.run_id = payload.run_id

    async def _finish(self, status: RunStatus) -> None:
        """Persist local terminal state; provider cancellation and usage may remain unknown."""
        if self.run_id is None:
            return
        with CancelScope(shield=True):
            await self.db.rollback()
            await self.db.execute(
                update(LlmRun)
                .where(
                    LlmRun.run_id == self.run_id,
                    LlmRun.status.in_([RunStatus.RUNNING, RunStatus.CANCEL_REQUESTED]),
                )
                .values(status=status, completed_at=utc_now())
            )
            await self.db.commit()

    async def _service(
        self, payload: ResponsesRequest
    ) -> ModelInvocationService | ResponseFileToolService:
        """Select the whole automatic attachment loop, not a single intermediate round."""
        if await has_automatic_file_tools(self.invocation, payload):
            return ResponseFileToolService(self.invocation)
        return self.invocation

    async def generate(self, payload: ResponsesRequest) -> ResponsesResult:
        """End nonstreaming chat turns on success, dispatch failure or cancellation."""
        await self._start(payload)
        try:
            result = await (await self._service(payload)).generate(payload)
            await self._finish(RunStatus.COMPLETED)
            return result
        except asyncio.CancelledError:
            await self._finish(RunStatus.CANCELLED)
            raise
        except Exception:
            await self._finish(RunStatus.FAILED)
            raise

    async def stream(self, payload: ResponsesRequest) -> AsyncIterator[StreamEvent]:
        """Commit terminal state before delivery; incomplete streams must also end the Run."""
        await self._start(payload)
        terminal = False
        try:
            service = await self._service(payload)
            async with aclosing(service.stream(payload)) as events:
                async for event in events:
                    if event.type in {StreamEventType.COMPLETED, StreamEventType.FAILED}:
                        await self._finish(
                            RunStatus.COMPLETED
                            if event.type is StreamEventType.COMPLETED
                            else RunStatus.FAILED
                        )
                        terminal = True
                    yield event
        except (asyncio.CancelledError, GeneratorExit):
            if not terminal:
                await self._finish(RunStatus.CANCELLED)
                terminal = True
            raise
        finally:
            if not terminal:
                await self._finish(RunStatus.FAILED)
