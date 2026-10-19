"""Run automatic read-only attachment tools for quick replies without an Agent workflow.

Each model round keeps its own Attempt and Context. The first Attempt anchors one
public SSE log across rounds; only the final answer emits response.completed.
No model-supplied paths, commands or tool definitions are executed.
"""

import asyncio
import hashlib
import json
from collections.abc import AsyncIterator
from contextlib import aclosing
from datetime import UTC, datetime
from decimal import Decimal
from time import monotonic

from nexuspilot_models.contracts import Message, MessageRole, StreamEvent, StreamEventType
from nexuspilot_models.errors import ModelProviderError
from sqlalchemy import select

from nexuspilot_api.core.errors import InvalidRequestError
from nexuspilot_api.models import LlmProviderContinuationState
from nexuspilot_api.schemas.responses import ResponsesRequest, ResponsesResult, ResponseUsage
from nexuspilot_api.services.agent_file_tool_service import AgentFileToolService, file_tool_guidance
from nexuspilot_api.services.conversation_file_service import conversation_file_ids
from nexuspilot_api.services.model_reasoning_service import record_model_response_event
from nexuspilot_api.services.model_response_service import ModelInvocationService
from nexuspilot_api.services.tool_context import ToolProgressTracker, allow_tool_request


async def has_automatic_file_tools(
    service: ModelInvocationService, payload: ResponsesRequest
) -> bool:
    """Enable native file reads for saved conversation turns, not external tool orchestration."""
    if payload.task_id or not payload.current_user_message_id or payload.tools:
        return False
    return bool(
        await conversation_file_ids(
            service.db_session,
            payload.run_id,
            current_user_message_id=payload.current_user_message_id,
        )
    )


def aggregate_response_usage(results: list[ResponsesResult]) -> ResponseUsage:
    """Sum reported usage across actual model rounds; preserve unknown totals as null."""
    totals = {}
    for name in ResponseUsage.model_fields:
        values = [getattr(result.usage, name) for result in results]
        if any(value is None for value in values):
            totals[name] = None
        elif name == "estimated_cost":
            totals[name] = str(sum((Decimal(value) for value in values), Decimal(0)))
        else:
            totals[name] = sum(values)
    return ResponseUsage(**totals)


class ResponseFileToolService:
    """Bind a single response's tool policy, history and durable public event sequence."""

    def __init__(self, invocation: ModelInvocationService):
        """Reuse the same authorized storage and DB dependencies as Agent file tools."""
        self.invocation = invocation
        self.tools = AgentFileToolService(invocation.db_session, invocation.storage)
        self.history: list[Message] = []
        self.records = []
        self.seen = {}
        self.progress = ToolProgressTracker()
        self.root_attempt_id: str | None = None
        self.sequence = 0
        self.results: list[ResponsesResult] = []

    async def generate(self, payload: ResponsesRequest) -> ResponsesResult:
        """Execute native rounds without streaming and return only the final response."""
        async with aclosing(self.stream(payload)) as events:
            async for event in events:
                if event.type is StreamEventType.COMPLETED:
                    return ResponsesResult.model_validate(event.data["response"])
                if event.type is StreamEventType.FAILED:
                    error = event.data["error"]
                    raise ModelProviderError(error["type"], error["message"])
        raise ModelProviderError("response_parse_error", "文件读取未生成最终回答。")

    async def _round_events(self, payload: ResponsesRequest) -> AsyncIterator[StreamEvent]:
        """Keep native streaming incremental, with a separate durable Attempt for every round."""
        if payload.stream:
            async with aclosing(
                self.invocation.stream(
                    payload,
                    tool_messages=self.history,
                    record_events=False,
                    request_type="file_tool_round",
                )
            ) as events:
                async for event in events:
                    yield event
        else:
            result = await self.invocation.generate(
                payload,
                tool_messages=self.history,
                request_type="file_tool_round",
            )
            yield StreamEvent(
                type=StreamEventType.COMPLETED,
                sequence=1,
                response_id=result.id,
                data={"attempt_id": result.id, "response": result.model_dump(mode="json")},
            )

    async def _publish(self, event: StreamEvent, payload: ResponsesRequest) -> StreamEvent:
        """Persist each public event before delivery under one stable replay cursor."""
        assert self.root_attempt_id is not None
        self.sequence += 1
        public = event.model_copy(
            update={
                "sequence": self.sequence,
                "response_id": self.root_attempt_id,
                "run_id": payload.run_id,
                "timestamp_ms": int(datetime.now(UTC).timestamp() * 1000),
                "data": {
                    **event.data,
                    "source_attempt_id": event.data.get("attempt_id"),
                    "attempt_id": self.root_attempt_id,
                },
            }
        )
        await record_model_response_event(
            self.invocation.db_session, attempt_id=self.root_attempt_id, event=public
        )
        return public

    async def stream(self, payload: ResponsesRequest) -> AsyncIterator[StreamEvent]:
        """Continue while tools make progress, honoring cancellation and optional deadline."""
        deadline = (
            monotonic() + payload.timeout_seconds if payload.timeout_seconds is not None else None
        )
        try:
            index = -1
            while True:
                index += 1
                await asyncio.sleep(0)
                await self.tools.validate_history(payload.run_id, self.records)
                remaining = deadline - monotonic() if deadline is not None else None
                if remaining is not None and remaining < 1:
                    raise InvalidRequestError("文件读取已超时，请缩小问题范围后重试。")
                definitions = self.tools.definitions()
                versions = {
                    tool.name: self.tools.loaded_versions[tool.name] for tool in definitions
                }
                key = payload.idempotency_key
                if key and index:
                    key = hashlib.sha256(f"{key}:file-round:{index}".encode()).hexdigest()
                round_payload = payload.model_copy(
                    update={
                        "tools": definitions,
                        "timeout_seconds": remaining,
                        "idempotency_key": key,
                        "instructions": "\n".join(filter(None, [payload.instructions,
                            file_tool_guidance(final_format="a user-facing answer"),
                            "For quick replies submit a user-facing answer, not node JSON. "
                            "Cite actual blocks; state unread/unsupported points explicitly."])),
                    }
                )
                response = None
                async with aclosing(self._round_events(round_payload)) as events:
                    async for event in events:
                        if self.root_attempt_id is None:
                            self.root_attempt_id = event.response_id
                        if event.type is StreamEventType.COMPLETED:
                            response = ResponsesResult.model_validate(event.data["response"])
                            self.results.append(response)
                            continue
                        # Native call deltas remain in the private raw response/call records.
                        if event.type in {StreamEventType.TOOL_CALL_DELTA, StreamEventType.USAGE}:
                            continue
                        if event.type is StreamEventType.STARTED and self.sequence:
                            continue
                        yield await self._publish(event, payload)
                        if event.type is StreamEventType.FAILED:
                            return
                if response is None:
                    raise InvalidRequestError("文件读取响应中断，未收到完整结果。")
                if not response.tool_calls:
                    final = response.model_copy(
                        update={
                            "usage": aggregate_response_usage(self.results),
                            "latency_ms": sum(result.latency_ms for result in self.results),
                        }
                    )
                    yield await self._publish(
                        StreamEvent(
                            type=StreamEventType.COMPLETED,
                            sequence=1,
                            data={
                                "attempt_id": response.id,
                                "response": final.model_dump(mode="json"),
                            },
                        ),
                        payload,
                    )
                    return
                if response.finish_reason not in {"tool_calls", "stop"}:
                    raise InvalidRequestError("文件工具请求不完整，请重试。")
                calls = response.tool_calls
                if len(calls) > 8 or len({call.id for call in calls}) != len(calls):
                    raise InvalidRequestError("文件工具请求数量或标识不合法。")
                assistant = Message(
                    role=MessageRole.ASSISTANT, content=response.output_text or "", tool_calls=calls
                )
                continuation = await self.invocation.db_session.scalar(
                    select(LlmProviderContinuationState.parent_attempt_id).where(
                        LlmProviderContinuationState.parent_attempt_id == response.id,
                    )
                )
                if continuation:
                    assistant._provider_continuation_state = (
                        await self.invocation.continuation_store.load_state(
                            parent_attempt_id=continuation,
                            run_id=payload.run_id,
                            task_id=None,
                            provider=response.provider,
                            model=response.model,
                        )
                    )
                self.history.append(assistant)
                for call in calls:
                    fingerprint = json.dumps([call.name, call.arguments, versions], sort_keys=True)
                    if not allow_tool_request(self.seen, call.name, fingerprint, len(self.records)):
                        raise InvalidRequestError(
                            "模型重复读取相同文件位置且没有进展，请调整问题后重试。"
                        )
                    async with asyncio.timeout(
                        max(0, deadline - monotonic()) if deadline is not None else None
                    ):
                        record = await self.tools.execute(
                            attempt_id=response.id, call=call, offered_versions=versions
                        )
                    self.records.append(record)
                    if not self.progress.observe(call.name, call.arguments, record.output_json):
                        raise InvalidRequestError(
                            "文件工具重复返回相同证据或持续搜索无结果，已停止继续调用。"
                        )
                    self.history.append(
                        Message(
                            role=MessageRole.TOOL,
                            tool_call_id=call.id,
                            tool_name=call.name,
                            content=json.dumps(record.output_json, ensure_ascii=False),
                        )
                    )
        except asyncio.CancelledError:
            # Model/Tool services retain their own cancellation facts; no new round is launched.
            raise
        except Exception as error:
            if self.root_attempt_id is None:
                raise
            message = (
                error.message
                if isinstance(error, ModelProviderError)
                else (
                    str(error)
                    if isinstance(error, InvalidRequestError)
                    else "文件读取失败，请稍后重试。"
                )
            )
            yield await self._publish(
                StreamEvent(
                    type=StreamEventType.FAILED,
                    sequence=1,
                    data={"error": {"type": "file_tool_response_failed", "message": message}},
                ),
                payload,
            )
