"""Coordinate provider calls with durable attempt, retry, cost, and raw-object records."""

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal

from fastapi import HTTPException, status
from nexuspilot_models.contracts import (
    ModelResponse,
    ProviderName,
    StreamEvent,
    StreamEventType,
)
from nexuspilot_models.errors import ModelProviderError
from nexuspilot_models.pricing import PriceCatalog
from nexuspilot_models.registry import ProviderRegistry
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from nexuspilot_api.models import (
    AttemptStatus,
    LlmAttempt,
    LlmAttemptRetry,
    LlmRun,
    new_id,
)
from nexuspilot_api.response_schemas import ResponsesRequest, ResponsesResult, ResponseUsage
from nexuspilot_api.service import require_run, require_task
from nexuspilot_api.storage import ObjectStorage


class ModelInvocationService:
    """Execute a routed model request while keeping MySQL as the source of execution truth."""

    def __init__(
        self,
        *,
        registry: ProviderRegistry,
        prices: PriceCatalog,
        session: AsyncSession,
        storage: ObjectStorage,
    ) -> None:
        """Bind application-level provider, pricing, persistence, and storage dependencies."""

        self.registry = registry
        self.prices = prices
        self.session = session
        self.storage = storage

    async def generate(self, payload: ResponsesRequest) -> ResponsesResult:
        """Execute a non-streaming response and finalize its durable attempt record."""

        request = payload.to_model_request()
        attempt = await self._start_attempt(payload)
        try:
            provider = self.registry.resolve(request.provider, request.model)
            response = await provider.generate(request)
            response = self._apply_price(response, request.provider, request.model)
            await self._complete_attempt(attempt, response)
            return self._to_result(attempt.attempt_id, request.provider, request.model, response)
        except ModelProviderError as error:
            await self._fail_attempt(attempt, error)
            raise
        except Exception as exc:
            error = ModelProviderError("internal_error", "Model response processing failed.")
            await self._fail_attempt(attempt, error)
            raise error from exc

    async def stream(self, payload: ResponsesRequest) -> AsyncIterator[StreamEvent]:
        """Stream normalized events and finalize success, failure, timeout, or cancellation."""

        request = payload.to_model_request()
        attempt = await self._start_attempt(payload)
        sequence = 1
        try:
            provider = self.registry.resolve(request.provider, request.model)
            async for event in provider.stream(request):
                event_data = {**event.data, "attempt_id": attempt.attempt_id}
                if event.type is StreamEventType.COMPLETED:
                    response_data = event_data.get("response")
                    if not isinstance(response_data, dict):
                        raise ModelProviderError(
                            "response_parse_error",
                            "Provider stream completed without a normalized response.",
                        )
                    response = ModelResponse.model_validate(response_data)
                    response = self._apply_price(response, request.provider, request.model)
                    await self._complete_attempt(attempt, response)
                    event_data["response"] = self._to_result(
                        attempt.attempt_id,
                        request.provider,
                        request.model,
                        response,
                    ).model_dump(mode="json")
                sequence = max(sequence, event.sequence)
                yield event.model_copy(update={"data": event_data})
        except asyncio.CancelledError:
            await self._cancel_attempt(attempt)
            raise
        except ModelProviderError as error:
            await self._fail_attempt(attempt, error)
            yield StreamEvent(
                type=StreamEventType.FAILED,
                sequence=sequence + 1,
                data={
                    "attempt_id": attempt.attempt_id,
                    "error": {"type": error.error_type, "message": error.message},
                },
            )
        except Exception:
            error = ModelProviderError("internal_error", "Model stream processing failed.")
            await self._fail_attempt(attempt, error)
            yield StreamEvent(
                type=StreamEventType.FAILED,
                sequence=sequence + 1,
                data={
                    "attempt_id": attempt.attempt_id,
                    "error": {"type": error.error_type, "message": error.message},
                },
            )

    async def _start_attempt(self, payload: ResponsesRequest) -> LlmAttempt:
        """Validate ownership, prevent duplicate request keys, and persist a started attempt."""

        await require_run(self.session, payload.run_id)
        if payload.task_id:
            task = await require_task(self.session, payload.task_id)
            if task.run_id != payload.run_id:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="Task does not belong to the run",
                )
        if payload.idempotency_key:
            existing = await self.session.scalar(
                select(LlmAttempt).where(LlmAttempt.request_key == payload.idempotency_key)
            )
            if existing:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Idempotency key already belongs to attempt {existing.attempt_id}",
                )
        attempt = LlmAttempt(
            attempt_id=new_id(),
            run_id=payload.run_id,
            task_id=payload.task_id,
            provider=payload.provider.value,
            model=payload.model,
            request_type="stream" if payload.stream else "generation",
            request_key=payload.idempotency_key,
            retry_count=0,
            status=AttemptStatus.STARTED,
        )
        self.session.add(attempt)
        await self.session.commit()
        try:
            raw_request = payload.model_dump(mode="json", exclude={"idempotency_key"})
            stored = await self.storage.put_bytes(
                f"{payload.run_id}/{attempt.attempt_id}/raw-request.json",
                json.dumps(raw_request, ensure_ascii=False).encode(),
                "application/json",
            )
            attempt.raw_request_uri = stored.uri
            await self.session.commit()
        except Exception as exc:
            attempt.status = AttemptStatus.FAILED
            attempt.error_code = "object_storage_error"
            attempt.error_message = "Failed to persist the raw model request."
            attempt.completed_at = datetime.now(UTC)
            await self.session.commit()
            raise ModelProviderError(
                "internal_error",
                "Failed to persist the raw model request.",
            ) from exc
        return attempt

    async def _complete_attempt(self, attempt: LlmAttempt, response: ModelResponse) -> None:
        """Persist raw response, physical attempts, accounting, and completed state atomically."""

        stored = await self.storage.put_bytes(
            f"{attempt.run_id}/{attempt.attempt_id}/raw-response.json",
            json.dumps(response.raw_response, ensure_ascii=False).encode(),
            "application/json",
        )
        attempt.status = AttemptStatus.COMPLETED
        attempt.input_tokens = response.input_tokens
        attempt.output_tokens = response.output_tokens
        attempt.cached_tokens = response.cached_tokens
        attempt.estimated_cost = (
            Decimal(response.estimated_cost) if response.estimated_cost is not None else None
        )
        attempt.latency_ms = response.latency_ms
        attempt.provider_request_id = response.provider_request_id
        attempt.raw_response_uri = stored.uri
        attempt.retry_count = max(0, len(response.transport_attempts) - 1)
        attempt.completed_at = datetime.now(UTC)
        self._add_transport_attempts(attempt.attempt_id, response.transport_attempts)
        if attempt.estimated_cost is not None:
            await self.session.execute(
                update(LlmRun)
                .where(LlmRun.run_id == attempt.run_id)
                .values(cost_used=LlmRun.cost_used + attempt.estimated_cost)
            )
        await self.session.commit()

    async def _fail_attempt(self, attempt: LlmAttempt, error: ModelProviderError) -> None:
        """Persist safe provider failure evidence and all completed physical HTTP attempts."""

        attempt.status = (
            AttemptStatus.TIMED_OUT if error.error_type == "timeout" else AttemptStatus.FAILED
        )
        attempt.error_code = error.error_type
        attempt.error_message = error.message[:4000]
        attempt.provider_request_id = error.provider_request_id
        attempt.retry_count = max(0, len(error.transport_attempts) - 1)
        attempt.completed_at = datetime.now(UTC)
        self._add_transport_attempts(attempt.attempt_id, error.transport_attempts)
        if error.raw_error:
            try:
                stored = await self.storage.put_bytes(
                    f"{attempt.run_id}/{attempt.attempt_id}/raw-error.json",
                    json.dumps(error.raw_error, ensure_ascii=False).encode(),
                    "application/json",
                )
                attempt.raw_response_uri = stored.uri
            except Exception:
                pass
        await self.session.commit()

    async def _cancel_attempt(self, attempt: LlmAttempt) -> None:
        """Mark a client-disconnected stream as cancelled before releasing its session."""

        attempt.status = AttemptStatus.CANCELLED
        attempt.error_code = "cancelled"
        attempt.error_message = "Client disconnected before the stream completed."
        attempt.completed_at = datetime.now(UTC)
        await self.session.commit()

    def _add_transport_attempts(self, attempt_id: str, attempts: list) -> None:
        """Attach physical HTTP attempt traces to the current database transaction."""

        self.session.add_all(
            LlmAttemptRetry(
                attempt_id=attempt_id,
                attempt_index=item.attempt_index,
                status_code=item.status_code,
                latency_ms=item.latency_ms,
                error_type=item.error_type,
                error_message=item.error_message,
            )
            for item in attempts
        )

    def _apply_price(
        self,
        response: ModelResponse,
        provider: ProviderName,
        model: str,
    ) -> ModelResponse:
        """Attach an estimate only when explicit pricing and complete usage are available."""

        estimated = self.prices.estimate(
            provider,
            model,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            cached_tokens=response.cached_tokens,
        )
        return response.model_copy(
            update={"estimated_cost": str(estimated) if estimated is not None else None}
        )

    def _to_result(
        self,
        attempt_id: str,
        provider: ProviderName,
        model: str,
        response: ModelResponse,
    ) -> ResponsesResult:
        """Remove native payload and transport traces from the public completed response."""

        return ResponsesResult(
            id=attempt_id,
            provider=provider,
            model=model,
            output_text=response.text,
            tool_calls=response.tool_calls,
            structured_output=response.structured_output,
            finish_reason=response.finish_reason,
            usage=ResponseUsage(
                input_tokens=response.input_tokens,
                output_tokens=response.output_tokens,
                cached_tokens=response.cached_tokens,
                estimated_cost=response.estimated_cost,
            ),
            latency_ms=response.latency_ms,
            provider_request_id=response.provider_request_id,
        )
