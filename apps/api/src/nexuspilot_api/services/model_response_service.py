"""Coordinate provider calls with durable model and transport-attempt records."""

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

from nexuspilot_api.infrastructure.object_storage import ObjectStorage
from nexuspilot_api.models import (
    AttemptStatus,
    LlmModelAttempt,
    LlmModelTransportAttempt,
    LlmRun,
    new_id,
)
from nexuspilot_api.schemas.responses import ResponsesRequest, ResponsesResult, ResponseUsage
from nexuspilot_api.services.lookups import require_run, require_task


class ModelInvocationService:
    """Execute a routed model request while keeping MySQL as the source of execution truth."""

    def __init__(
        self,
        *,
        registry: ProviderRegistry,
        prices: PriceCatalog,
        db_session: AsyncSession,
        storage: ObjectStorage,
    ) -> None:
        """Bind application-level provider, pricing, persistence, and storage dependencies."""

        self.registry = registry
        self.prices = prices
        self.db_session = db_session
        self.storage = storage

    async def generate(self, payload: ResponsesRequest) -> ResponsesResult:
        """Execute a response and finalize its durable LLM model invocation record."""

        request = payload.to_model_request()
        model_attempt = await self._start_model_attempt(payload)
        try:
            provider = self.registry.resolve(request.provider, request.model)
            response = await provider.generate(request)
            response = self._apply_price(response, request.provider, request.model)
            await self._complete_model_attempt(model_attempt, response)
            return self._to_result(
                model_attempt.attempt_id,
                request.provider,
                request.model,
                response,
            )
        except ModelProviderError as error:
            await self._fail_model_attempt(model_attempt, error)
            raise
        except Exception as exc:
            error = ModelProviderError("internal_error", "Model response processing failed.")
            await self._fail_model_attempt(model_attempt, error)
            raise error from exc

    async def stream(self, payload: ResponsesRequest) -> AsyncIterator[StreamEvent]:
        """Stream normalized events and finalize success, failure, timeout, or cancellation."""

        request = payload.to_model_request()
        model_attempt = await self._start_model_attempt(payload)
        sequence = 1
        try:
            provider = self.registry.resolve(request.provider, request.model)
            async for event in provider.stream(request):
                event_data = {**event.data, "attempt_id": model_attempt.attempt_id}
                if event.type is StreamEventType.COMPLETED:
                    response_data = event_data.get("response")
                    if not isinstance(response_data, dict):
                        raise ModelProviderError(
                            "response_parse_error",
                            "Provider stream completed without a normalized response.",
                        )
                    response = ModelResponse.model_validate(response_data)
                    response = self._apply_price(response, request.provider, request.model)
                    await self._complete_model_attempt(model_attempt, response)
                    event_data["response"] = self._to_result(
                        model_attempt.attempt_id,
                        request.provider,
                        request.model,
                        response,
                    ).model_dump(mode="json")
                sequence = max(sequence, event.sequence)
                yield event.model_copy(update={"data": event_data})
        except asyncio.CancelledError:
            await self._cancel_model_attempt(model_attempt)
            raise
        except ModelProviderError as error:
            await self._fail_model_attempt(model_attempt, error)
            yield StreamEvent(
                type=StreamEventType.FAILED,
                sequence=sequence + 1,
                data={
                    "attempt_id": model_attempt.attempt_id,
                    "error": {"type": error.error_type, "message": error.message},
                },
            )
        except Exception:
            error = ModelProviderError("internal_error", "Model stream processing failed.")
            await self._fail_model_attempt(model_attempt, error)
            yield StreamEvent(
                type=StreamEventType.FAILED,
                sequence=sequence + 1,
                data={
                    "attempt_id": model_attempt.attempt_id,
                    "error": {"type": error.error_type, "message": error.message},
                },
            )

    async def _start_model_attempt(self, payload: ResponsesRequest) -> LlmModelAttempt:
        """Validate ownership and persist a started LLM model invocation."""

        await require_run(self.db_session, payload.run_id)
        if payload.task_id:
            task = await require_task(self.db_session, payload.task_id)
            if task.run_id != payload.run_id:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="Task does not belong to the run",
                )
        if payload.idempotency_key:
            existing = await self.db_session.scalar(
                select(LlmModelAttempt).where(
                    LlmModelAttempt.request_key == payload.idempotency_key
                )
            )
            if existing:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        "Idempotency key already belongs to model attempt "
                        f"{existing.attempt_id}"
                    ),
                )
        model_attempt = LlmModelAttempt(
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
        self.db_session.add(model_attempt)
        await self.db_session.commit()
        try:
            raw_request = payload.model_dump(mode="json", exclude={"idempotency_key"})
            stored_raw_request = await self.storage.put_bytes(
                f"{payload.run_id}/{model_attempt.attempt_id}/raw-request.json",
                json.dumps(raw_request, ensure_ascii=False).encode(),
                "application/json",
            )
            model_attempt.raw_request_uri = stored_raw_request.uri
            await self.db_session.commit()
        except Exception as exc:
            model_attempt.status = AttemptStatus.FAILED
            model_attempt.error_code = "object_storage_error"
            model_attempt.error_message = "Failed to persist the raw model request."
            model_attempt.completed_at = datetime.now(UTC)
            await self.db_session.commit()
            raise ModelProviderError(
                "internal_error",
                "Failed to persist the raw model request.",
            ) from exc
        return model_attempt

    async def _complete_model_attempt(
        self,
        model_attempt: LlmModelAttempt,
        response: ModelResponse,
    ) -> None:
        """Persist raw response, physical attempts, accounting, and completed state atomically."""

        stored_raw_response = await self.storage.put_bytes(
            f"{model_attempt.run_id}/{model_attempt.attempt_id}/raw-response.json",
            json.dumps(response.raw_response, ensure_ascii=False).encode(),
            "application/json",
        )
        model_attempt.status = AttemptStatus.COMPLETED
        model_attempt.input_tokens = response.input_tokens
        model_attempt.output_tokens = response.output_tokens
        model_attempt.cached_tokens = response.cached_tokens
        model_attempt.estimated_cost = (
            Decimal(response.estimated_cost) if response.estimated_cost is not None else None
        )
        model_attempt.latency_ms = response.latency_ms
        model_attempt.provider_request_id = response.provider_request_id
        model_attempt.raw_response_uri = stored_raw_response.uri
        model_attempt.retry_count = max(0, len(response.transport_attempts) - 1)
        model_attempt.completed_at = datetime.now(UTC)
        self._add_provider_transport_attempts(
            model_attempt.attempt_id,
            response.transport_attempts,
        )
        if model_attempt.estimated_cost is not None:
            await self.db_session.execute(
                update(LlmRun)
                .where(LlmRun.run_id == model_attempt.run_id)
                .values(cost_used=LlmRun.cost_used + model_attempt.estimated_cost)
            )
        await self.db_session.commit()

    async def _fail_model_attempt(
        self,
        model_attempt: LlmModelAttempt,
        error: ModelProviderError,
    ) -> None:
        """Persist safe provider failure evidence and all completed physical HTTP attempts."""

        model_attempt.status = (
            AttemptStatus.TIMED_OUT if error.error_type == "timeout" else AttemptStatus.FAILED
        )
        model_attempt.error_code = error.error_type
        model_attempt.error_message = error.message[:4000]
        model_attempt.provider_request_id = error.provider_request_id
        model_attempt.retry_count = max(0, len(error.transport_attempts) - 1)
        model_attempt.completed_at = datetime.now(UTC)
        self._add_provider_transport_attempts(
            model_attempt.attempt_id,
            error.transport_attempts,
        )
        if error.raw_error:
            try:
                stored_raw_error = await self.storage.put_bytes(
                    f"{model_attempt.run_id}/{model_attempt.attempt_id}/raw-error.json",
                    json.dumps(error.raw_error, ensure_ascii=False).encode(),
                    "application/json",
                )
                model_attempt.raw_response_uri = stored_raw_error.uri
            except Exception:
                pass
        await self.db_session.commit()

    async def _cancel_model_attempt(self, model_attempt: LlmModelAttempt) -> None:
        """Mark a client-disconnected stream as cancelled before releasing its database session."""

        model_attempt.status = AttemptStatus.CANCELLED
        model_attempt.error_code = "cancelled"
        model_attempt.error_message = "Client disconnected before the stream completed."
        model_attempt.completed_at = datetime.now(UTC)
        await self.db_session.commit()

    def _add_provider_transport_attempts(
        self,
        model_attempt_id: str,
        provider_transport_attempts: list,
    ) -> None:
        """Attach provider HTTP transport attempts to the database transaction."""

        self.db_session.add_all(
            LlmModelTransportAttempt(
                attempt_id=model_attempt_id,
                attempt_index=provider_transport_attempt.attempt_index,
                status_code=provider_transport_attempt.status_code,
                latency_ms=provider_transport_attempt.latency_ms,
                error_type=provider_transport_attempt.error_type,
                error_message=provider_transport_attempt.error_message,
            )
            for provider_transport_attempt in provider_transport_attempts
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
