"""Coordinate provider calls with durable model and transport-attempt records."""

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from fastapi import HTTPException, status
from nexuspilot_models.contracts import (
    ModelReasoningCapabilities,
    ModelRequest,
    ModelResponse,
    ProviderContinuationState,
    ProviderName,
    ReasoningBlockStatus,
    ReasoningDisplayPolicy,
    ReasoningPresentation,
    ReasoningPresentationKind,
    StreamEvent,
    StreamEventType,
)
from nexuspilot_models.errors import ModelProviderError
from nexuspilot_models.pricing import PriceCatalog
from nexuspilot_models.registry import ProviderRegistry
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from nexuspilot_api.core.config import Settings, get_settings
from nexuspilot_api.infrastructure.object_storage import ObjectStorage
from nexuspilot_api.infrastructure.provider_continuation_store import (
    ProviderContinuationStateError,
    ProviderContinuationStore,
)
from nexuspilot_api.models import (
    AttemptStatus,
    LlmModelAttempt,
    LlmModelTransportAttempt,
    LlmProviderContinuationState,
    LlmReasoningBlock,
    LlmRun,
    new_id,
)
from nexuspilot_api.schemas.responses import ResponsesRequest, ResponsesResult, ResponseUsage
from nexuspilot_api.services.lookups import require_run, require_task
from nexuspilot_api.services.model_reasoning_service import (
    project_reasoning_presentation,
    project_reasoning_stream_event,
    record_model_response_event,
)


class ModelInvocationService:
    """Execute a routed model request while keeping MySQL as the source of execution truth."""

    def __init__(
        self,
        *,
        registry: ProviderRegistry,
        prices: PriceCatalog,
        db_session: AsyncSession,
        storage: ObjectStorage,
        settings: Settings | None = None,
    ) -> None:
        """Bind Provider and persistence dependencies with validated settings."""

        selected_settings = settings or get_settings()
        self.registry = registry
        self.prices = prices
        self.db_session = db_session
        self.storage = storage
        self.settings = selected_settings
        self.continuation_store = ProviderContinuationStore(
            db_session=db_session,
            storage=storage,
            settings=selected_settings,
        )

    async def generate(self, payload: ResponsesRequest) -> ResponsesResult:
        """Execute a response and finalize its durable LLM model invocation record."""

        request = payload.to_model_request()
        model_attempt = await self._start_model_attempt(payload)
        provider_dispatch_may_have_started = False
        try:
            provider = self.registry.resolve(request.provider, request.model)
            capabilities = self.registry.reasoning_capabilities(
                request.provider,
                request.model,
            )
            request = await self._load_provider_continuation(payload, request)
            # The platform deadline remains authoritative even when a provider
            # adapter or custom transport fails to enforce its own timeout.
            provider_dispatch_may_have_started = True
            async with asyncio.timeout(payload.timeout_seconds):
                response = await provider.generate(request)
            response = self._apply_price(response, request.provider, request.model)
            response = self._project_response_reasoning(
                response,
                attempt_id=model_attempt.attempt_id,
                capabilities=capabilities,
                display_policy=payload.reasoning_display_policy,
            )
            await self._complete_model_attempt_resiliently(model_attempt, response)
            return self._to_result(
                model_attempt.attempt_id,
                request.provider,
                request.model,
                response,
            )
        except TimeoutError as exc:
            error = ModelProviderError(
                "timeout",
                "Model provider request timed out.",
                retryable=True,
            )
            await self._fail_model_attempt(model_attempt, error)
            raise error from exc
        except asyncio.CancelledError:
            if provider_dispatch_may_have_started:
                await self._mark_model_attempt_outcome_unknown(model_attempt)
            else:
                await self._cancel_model_attempt(model_attempt)
            raise
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
        sequence = 0
        active_reasoning_blocks: dict[str, dict[str, str]] = {}
        provider_dispatch_may_have_started = False
        try:
            provider = self.registry.resolve(request.provider, request.model)
            capabilities = self.registry.reasoning_capabilities(
                request.provider,
                request.model,
            )
            request = await self._load_provider_continuation(payload, request)
            provider_dispatch_may_have_started = True
            async for event in self._stream_provider_with_deadline(
                provider,
                request,
                timeout_seconds=payload.timeout_seconds,
            ):
                event = self._scope_reasoning_event(event, model_attempt.attempt_id)
                if event.type in {
                    StreamEventType.REASONING_STARTED,
                    StreamEventType.REASONING_RAW_DELTA,
                    StreamEventType.REASONING_SUMMARY_DELTA,
                    StreamEventType.REASONING_COMPLETED,
                    StreamEventType.REASONING_INTERRUPTED,
                }:
                    projected_event = project_reasoning_stream_event(
                        event,
                        capabilities=capabilities,
                        display_policy=payload.reasoning_display_policy,
                    )
                    if projected_event is None:
                        continue
                    event = projected_event
                event_data = {**event.data, "attempt_id": model_attempt.attempt_id}
                if event.type is StreamEventType.USAGE and "usage" not in event_data:
                    event_data = {
                        "attempt_id": model_attempt.attempt_id,
                        "usage": event.data,
                    }
                self._update_active_reasoning_blocks(
                    active_reasoning_blocks,
                    event.type,
                    event_data,
                )
                if event.type is StreamEventType.COMPLETED:
                    response_data = event_data.get("response")
                    if not isinstance(response_data, dict):
                        raise ModelProviderError(
                            "response_parse_error",
                            "Provider stream completed without a normalized response.",
                        )
                    response = ModelResponse.model_validate(response_data)
                    private_continuation_data = event.private_data.get(
                        "provider_continuation_state"
                    )
                    if isinstance(private_continuation_data, dict):
                        response = response.model_copy(
                            update={
                                "provider_continuation_state": (
                                    ProviderContinuationState.model_validate(
                                        private_continuation_data
                                    )
                                )
                            }
                        )
                    response = self._apply_price(response, request.provider, request.model)
                    response = self._project_response_reasoning(
                        response,
                        attempt_id=model_attempt.attempt_id,
                        capabilities=capabilities,
                        display_policy=payload.reasoning_display_policy,
                    )
                    await self._complete_model_attempt_resiliently(model_attempt, response)
                    event_data["response"] = self._to_result(
                        model_attempt.attempt_id,
                        request.provider,
                        request.model,
                        response,
                    ).model_dump(mode="json")
                sequence += 1
                public_event = event.model_copy(
                    update={
                        "sequence": sequence,
                        "response_id": model_attempt.attempt_id,
                        "run_id": model_attempt.run_id,
                        "step_id": model_attempt.task_id,
                        "timestamp_ms": int(datetime.now(UTC).timestamp() * 1000),
                        "data": event_data,
                    }
                )
                await self._record_first_visible_token(model_attempt, public_event)
                await record_model_response_event(
                    self.db_session,
                    attempt_id=model_attempt.attempt_id,
                    event=public_event,
                )
                yield public_event
        except asyncio.CancelledError:
            if provider_dispatch_may_have_started:
                await self._mark_model_attempt_outcome_unknown(model_attempt)
            else:
                await self._cancel_model_attempt(model_attempt)
            raise
        except ModelProviderError as error:
            model_attempt_id = model_attempt.attempt_id
            model_attempt_run_id = model_attempt.run_id
            model_attempt_task_id = model_attempt.task_id
            model_attempt_started_at = model_attempt.started_at
            model_attempt_first_visible_token_at = model_attempt.first_visible_token_at
            await self._fail_model_attempt(model_attempt, error)
            sequence, interrupted_events = await self._record_interrupted_reasoning_events(
                active_reasoning_blocks,
                sequence=sequence,
                attempt_id=model_attempt_id,
                run_id=model_attempt_run_id,
                task_id=model_attempt_task_id,
                display_policy=payload.reasoning_display_policy,
                started_at=model_attempt_started_at,
                first_visible_token_at=model_attempt_first_visible_token_at,
            )
            for interrupted_event in interrupted_events:
                yield interrupted_event
            failure_event = StreamEvent(
                type=StreamEventType.FAILED,
                sequence=sequence + 1,
                response_id=model_attempt_id,
                run_id=model_attempt_run_id,
                step_id=model_attempt_task_id,
                timestamp_ms=int(datetime.now(UTC).timestamp() * 1000),
                data={
                    "attempt_id": model_attempt_id,
                    "error": {"type": error.error_type, "message": error.message},
                },
            )
            await record_model_response_event(
                self.db_session,
                attempt_id=model_attempt_id,
                event=failure_event,
            )
            yield failure_event
        except Exception:
            model_attempt_id = model_attempt.attempt_id
            model_attempt_run_id = model_attempt.run_id
            model_attempt_task_id = model_attempt.task_id
            model_attempt_started_at = model_attempt.started_at
            model_attempt_first_visible_token_at = model_attempt.first_visible_token_at
            error = ModelProviderError("internal_error", "Model stream processing failed.")
            await self._fail_model_attempt(model_attempt, error)
            sequence, interrupted_events = await self._record_interrupted_reasoning_events(
                active_reasoning_blocks,
                sequence=sequence,
                attempt_id=model_attempt_id,
                run_id=model_attempt_run_id,
                task_id=model_attempt_task_id,
                display_policy=payload.reasoning_display_policy,
                started_at=model_attempt_started_at,
                first_visible_token_at=model_attempt_first_visible_token_at,
            )
            for interrupted_event in interrupted_events:
                yield interrupted_event
            failure_event = StreamEvent(
                type=StreamEventType.FAILED,
                sequence=sequence + 1,
                response_id=model_attempt_id,
                run_id=model_attempt_run_id,
                step_id=model_attempt_task_id,
                timestamp_ms=int(datetime.now(UTC).timestamp() * 1000),
                data={
                    "attempt_id": model_attempt_id,
                    "error": {"type": error.error_type, "message": error.message},
                },
            )
            await record_model_response_event(
                self.db_session,
                attempt_id=model_attempt_id,
                event=failure_event,
            )
            yield failure_event

    async def _stream_provider_with_deadline(
        self,
        provider: Any,
        request: ModelRequest,
        *,
        timeout_seconds: float,
    ) -> AsyncIterator[StreamEvent]:
        """Enforce one total stream deadline and normalize expiry as a retryable error."""

        try:
            async with asyncio.timeout(timeout_seconds):
                async for event in provider.stream(request):
                    yield event
        except TimeoutError as exc:
            raise ModelProviderError(
                "timeout",
                "Model provider stream timed out.",
                retryable=True,
            ) from exc

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
                        f"Idempotency key already belongs to model attempt {existing.attempt_id}"
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
            reasoning_display_policy=payload.reasoning_display_policy.value,
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
        except asyncio.CancelledError:
            await self._cancel_model_attempt(model_attempt)
            raise
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

        sanitized_raw_response = self._sanitize_raw_response_for_audit(
            response.raw_response
        )
        try:
            stored_raw_response = await self.storage.put_bytes(
                f"{model_attempt.run_id}/{model_attempt.attempt_id}/raw-response.json",
                json.dumps(sanitized_raw_response, ensure_ascii=False).encode(),
                "application/json",
            )
        except Exception as exc:
            try:
                await self._record_response_audit_failure(model_attempt, response)
            except Exception as persistence_error:
                await self._record_response_persistence_unknown(
                    model_attempt,
                    response,
                    raw_response_uri=None,
                )
                raise ModelProviderError(
                    "response_persistence_error",
                    "Provider responded, but its accounting state could not be confirmed.",
                    provider_request_id=response.provider_request_id,
                    transport_attempts=response.transport_attempts,
                ) from persistence_error
            raise ModelProviderError(
                "response_audit_failed",
                "Provider responded, but the raw response audit record could not be stored.",
                provider_request_id=response.provider_request_id,
                transport_attempts=response.transport_attempts,
            ) from exc
        prepared_continuation_state: LlmProviderContinuationState | None = None
        if response.provider_continuation_state is not None:
            try:
                prepared_continuation_state = (
                    await self.continuation_store.prepare_persisted_state(
                        parent_attempt_id=model_attempt.attempt_id,
                        run_id=model_attempt.run_id,
                        task_id=model_attempt.task_id,
                        provider=ProviderName(model_attempt.provider),
                        model=model_attempt.model,
                        state=response.provider_continuation_state,
                    )
                )
            except Exception as exc:
                await self._record_response_audit_failure(model_attempt, response)
                raise ModelProviderError(
                    "continuation_persistence_error",
                    "Provider responded, but private continuation state could not be stored.",
                    provider_request_id=response.provider_request_id,
                    transport_attempts=response.transport_attempts,
                ) from exc
        try:
            await self._persist_provider_response_state(
                model_attempt,
                response,
                status=AttemptStatus.COMPLETED,
                raw_response_uri=stored_raw_response.uri,
                continuation_state=prepared_continuation_state,
            )
        except Exception as exc:
            durable_status = await self._record_response_persistence_unknown(
                model_attempt,
                response,
                raw_response_uri=stored_raw_response.uri,
            )
            if durable_status == AttemptStatus.COMPLETED:
                return
            raise ModelProviderError(
                "response_persistence_error",
                "Provider responded, but its accounting state could not be confirmed.",
                provider_request_id=response.provider_request_id,
                transport_attempts=response.transport_attempts,
            ) from exc

    async def _complete_model_attempt_resiliently(
        self,
        model_attempt: LlmModelAttempt,
        response: ModelResponse,
    ) -> None:
        """Finish a known provider response before propagating caller cancellation."""

        completion_task = asyncio.create_task(self._complete_model_attempt(model_attempt, response))
        try:
            await asyncio.shield(completion_task)
        except asyncio.CancelledError:
            # Do not let request cancellation interrupt the accounting transaction
            # after the provider has returned a known response. Waiting here also
            # prevents concurrent use of this request-scoped AsyncSession.
            try:
                await completion_task
            except Exception:
                # The completion task persists its own explicit audit-failure state.
                # Cancellation remains the caller-visible outcome.
                pass
            raise

    async def _record_response_audit_failure(
        self,
        model_attempt: LlmModelAttempt,
        response: ModelResponse,
    ) -> None:
        """Preserve known provider accounting when raw response storage is unavailable."""

        await self._persist_provider_response_state(
            model_attempt,
            response,
            status=AttemptStatus.FAILED,
            raw_response_uri=None,
            error_code="response_audit_failed",
            error_message=(
                "Provider responded, but the raw response audit record could not be stored."
            ),
            continuation_state=None,
        )

    async def _persist_provider_response_state(
        self,
        model_attempt: LlmModelAttempt,
        response: ModelResponse,
        *,
        status: AttemptStatus,
        raw_response_uri: str | None,
        error_code: str | None = None,
        error_message: str | None = None,
        continuation_state: LlmProviderContinuationState | None = None,
    ) -> None:
        """Commit response facts once, reconciling one ambiguous or transient failure."""

        attempt_id = model_attempt.attempt_id
        target_status = status
        for persistence_attempt in range(2):
            if persistence_attempt:
                await self.db_session.rollback()
                durable_attempt = await self.db_session.get(
                    LlmModelAttempt,
                    attempt_id,
                    populate_existing=True,
                )
                if durable_attempt is None:
                    raise RuntimeError("Model Attempt disappeared during response persistence")
                if durable_attempt.status == target_status:
                    return
                if durable_attempt.status != AttemptStatus.STARTED:
                    raise RuntimeError("Model Attempt reached a conflicting terminal state")
                model_attempt = durable_attempt

            self._apply_provider_response_facts(
                model_attempt,
                response,
                status=target_status,
                raw_response_uri=raw_response_uri,
                error_code=error_code,
                error_message=error_message,
            )
            self._add_provider_transport_attempts(
                model_attempt.attempt_id,
                response.transport_attempts,
            )
            self._add_reasoning_blocks(
                model_attempt,
                response,
            )
            if continuation_state is not None:
                self.db_session.add(continuation_state)
            if model_attempt.estimated_cost is not None:
                await self.db_session.execute(
                    update(LlmRun)
                    .where(LlmRun.run_id == model_attempt.run_id)
                    .values(cost_used=LlmRun.cost_used + model_attempt.estimated_cost)
                )
            try:
                await self.db_session.commit()
                return
            except Exception:
                if persistence_attempt:
                    await self.db_session.rollback()
                    durable_attempt = await self.db_session.get(
                        LlmModelAttempt,
                        attempt_id,
                        populate_existing=True,
                    )
                    if durable_attempt is not None and durable_attempt.status == target_status:
                        return
                    raise

        raise RuntimeError("Model response persistence retry was exhausted")

    def _apply_provider_response_facts(
        self,
        model_attempt: LlmModelAttempt,
        response: ModelResponse,
        *,
        status: AttemptStatus,
        raw_response_uri: str | None,
        error_code: str | None,
        error_message: str | None,
    ) -> None:
        """Assign normalized provider response and terminal audit fields to an Attempt."""

        model_attempt.status = status
        model_attempt.input_tokens = response.input_tokens
        model_attempt.output_tokens = response.output_tokens
        model_attempt.cached_tokens = response.cached_tokens
        model_attempt.reasoning_tokens = response.reasoning_tokens
        model_attempt.estimated_cost = (
            Decimal(response.estimated_cost) if response.estimated_cost is not None else None
        )
        model_attempt.latency_ms = response.latency_ms
        model_attempt.provider_request_id = response.provider_request_id
        model_attempt.raw_response_uri = raw_response_uri
        model_attempt.retry_count = max(0, len(response.transport_attempts) - 1)
        model_attempt.error_code = error_code
        model_attempt.error_message = error_message
        model_attempt.completed_at = datetime.now(UTC)

    async def _record_response_persistence_unknown(
        self,
        model_attempt: LlmModelAttempt,
        response: ModelResponse,
        *,
        raw_response_uri: str | None,
    ) -> AttemptStatus:
        """Preserve provider facts when the preceding completion commit stayed uncertain."""

        attempt_id = model_attempt.attempt_id
        await self.db_session.rollback()
        durable_attempt = await self.db_session.get(
            LlmModelAttempt,
            attempt_id,
            populate_existing=True,
        )
        if durable_attempt is None:
            raise RuntimeError("Model Attempt disappeared during persistence recovery")
        if durable_attempt.status != AttemptStatus.STARTED:
            return durable_attempt.status
        self._apply_provider_response_facts(
            durable_attempt,
            response,
            status=AttemptStatus.OUTCOME_UNKNOWN,
            raw_response_uri=raw_response_uri,
            error_code="response_persistence_error",
            error_message=(
                "Provider responded, but the final accounting transaction could not be confirmed."
            ),
        )
        self._add_provider_transport_attempts(
            durable_attempt.attempt_id,
            response.transport_attempts,
        )
        if durable_attempt.estimated_cost is not None:
            await self.db_session.execute(
                update(LlmRun)
                .where(LlmRun.run_id == durable_attempt.run_id)
                .values(cost_used=LlmRun.cost_used + durable_attempt.estimated_cost)
            )
        await self.db_session.commit()
        return AttemptStatus.OUTCOME_UNKNOWN

    async def _reload_started_model_attempt(
        self,
        model_attempt_id: str,
    ) -> LlmModelAttempt | None:
        """Rollback transient state and reload an Attempt only while it is still started."""

        await self.db_session.rollback()
        durable_attempt = await self.db_session.get(
            LlmModelAttempt,
            model_attempt_id,
            populate_existing=True,
        )
        if durable_attempt is None or durable_attempt.status != AttemptStatus.STARTED:
            # The terminal-state probe starts a read transaction. Streaming error
            # handling can finish after the response dependency stack has begun
            # unwinding, so close that transaction here instead of relying on a
            # later request-session finalizer to return the pooled connection.
            await self.db_session.rollback()
            return None
        return durable_attempt

    async def _fail_model_attempt(
        self,
        model_attempt: LlmModelAttempt,
        error: ModelProviderError,
    ) -> None:
        """Persist safe provider failure evidence and all completed physical HTTP attempts."""

        model_attempt = await self._reload_started_model_attempt(model_attempt.attempt_id)
        if model_attempt is None:
            return

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
        """Persist cancellation before a request or stream releases its database session."""

        attempt_id = model_attempt.attempt_id
        await self.db_session.rollback()
        await self.db_session.execute(
            update(LlmModelAttempt)
            .where(
                LlmModelAttempt.attempt_id == attempt_id,
                LlmModelAttempt.status == AttemptStatus.STARTED,
            )
            .values(
                status=AttemptStatus.CANCELLED,
                error_code="cancelled",
                error_message="Model invocation was cancelled before completion.",
                completed_at=datetime.now(UTC),
            )
            .execution_options(synchronize_session=False)
        )
        await self.db_session.commit()
        await self.db_session.get(LlmModelAttempt, attempt_id, populate_existing=True)

    async def _mark_model_attempt_outcome_unknown(
        self,
        model_attempt: LlmModelAttempt,
    ) -> None:
        """Record that provider dispatch cannot be excluded after caller cancellation."""

        attempt_id = model_attempt.attempt_id
        await self.db_session.rollback()
        await self.db_session.execute(
            update(LlmModelAttempt)
            .where(
                LlmModelAttempt.attempt_id == attempt_id,
                LlmModelAttempt.status == AttemptStatus.STARTED,
            )
            .values(
                status=AttemptStatus.OUTCOME_UNKNOWN,
                error_code="provider_outcome_unknown",
                error_message=(
                    "Model invocation was cancelled after entering the provider adapter; "
                    "dispatch and billing outcome cannot be confirmed."
                ),
                completed_at=datetime.now(UTC),
            )
            .execution_options(synchronize_session=False)
        )
        await self.db_session.commit()
        await self.db_session.get(LlmModelAttempt, attempt_id, populate_existing=True)

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

    def _add_reasoning_blocks(
        self,
        model_attempt: LlmModelAttempt,
        response: ModelResponse,
    ) -> None:
        """Attach final sanitized reasoning projections to the response transaction."""

        completed_at = datetime.now(UTC)
        for block_index, block in enumerate(response.reasoning_blocks):
            self.db_session.add(
                LlmReasoningBlock(
                    reasoning_block_id=block.block_id,
                    attempt_id=model_attempt.attempt_id,
                    block_index=block_index,
                    presentation_kind=block.kind.value,
                    status=block.status.value,
                    visible_text=block.text,
                    reasoning_tokens=block.reasoning_tokens,
                    display_policy=model_attempt.reasoning_display_policy,
                    final_event_sequence=None,
                    snapshot_version=1,
                    started_at=model_attempt.started_at,
                    first_visible_token_at=(
                        model_attempt.first_visible_token_at if block.text else None
                    ),
                    completed_at=completed_at,
                )
            )

    async def _load_provider_continuation(
        self,
        payload: ResponsesRequest,
        request: ModelRequest,
    ) -> ModelRequest:
        """Resolve an optional parent Attempt reference into trusted provider-only input."""

        if payload.continuation_from_attempt_id is None:
            return request
        try:
            continuation_state = await self.continuation_store.load_state(
                parent_attempt_id=payload.continuation_from_attempt_id,
                run_id=payload.run_id,
                task_id=payload.task_id,
                provider=payload.provider,
                model=payload.model,
            )
        except ProviderContinuationStateError as exc:
            raise ModelProviderError(
                "invalid_continuation_state",
                str(exc),
                retryable=False,
            ) from exc
        return request.model_copy(
            update={"provider_continuation_state": continuation_state}
        )

    def _project_response_reasoning(
        self,
        response: ModelResponse,
        *,
        attempt_id: str,
        capabilities: ModelReasoningCapabilities,
        display_policy: ReasoningDisplayPolicy,
    ) -> ModelResponse:
        """Apply disclosure policy and replace provider-local block IDs with scoped IDs."""

        projected_blocks: list[ReasoningPresentation] = []
        for block in response.reasoning_blocks:
            scoped_block = block.model_copy(
                update={"block_id": self._scoped_block_id(attempt_id, block.block_id)}
            )
            projected = project_reasoning_presentation(
                scoped_block,
                capabilities=capabilities,
                display_policy=display_policy,
            )
            if projected is not None:
                projected_blocks.append(projected)
        return response.model_copy(update={"reasoning_blocks": projected_blocks})

    def _scope_reasoning_event(
        self,
        event: StreamEvent,
        attempt_id: str,
    ) -> StreamEvent:
        """Make provider-local block identifiers unique within durable platform storage."""

        event_data = dict(event.data)
        block_id = event_data.get("block_id")
        if isinstance(block_id, str):
            event_data["block_id"] = self._scoped_block_id(attempt_id, block_id)
        block = event_data.get("block")
        if isinstance(block, dict) and isinstance(block.get("block_id"), str):
            event_data["block"] = {
                **block,
                "block_id": self._scoped_block_id(attempt_id, block["block_id"]),
            }
        return event.model_copy(update={"data": event_data})

    @staticmethod
    def _scoped_block_id(attempt_id: str, provider_block_id: str) -> str:
        """Build one deterministic globally unique block ID from trusted identifiers."""

        return f"{attempt_id}:{provider_block_id}"[:128]

    async def _record_first_visible_token(
        self,
        model_attempt: LlmModelAttempt,
        event: StreamEvent,
    ) -> None:
        """Record the first public text or reasoning delta in the event transaction."""

        if model_attempt.first_visible_token_at is not None:
            return
        if event.type not in {
            StreamEventType.TEXT_DELTA,
            StreamEventType.REASONING_RAW_DELTA,
            StreamEventType.REASONING_SUMMARY_DELTA,
        }:
            return
        delta = event.data.get("delta")
        if isinstance(delta, str) and delta:
            model_attempt.first_visible_token_at = datetime.now(UTC)

    @staticmethod
    def _update_active_reasoning_blocks(
        active_reasoning_blocks: dict[str, dict[str, str]],
        event_type: StreamEventType,
        event_data: dict[str, Any],
    ) -> None:
        """Track only sanitized public block state needed to finalize an interrupted stream."""

        if event_type is StreamEventType.REASONING_STARTED:
            block_id = event_data.get("block_id")
            kind = event_data.get("kind")
            if isinstance(block_id, str) and isinstance(kind, str):
                active_reasoning_blocks[block_id] = {"kind": kind, "text": ""}
            return
        if event_type in {
            StreamEventType.REASONING_RAW_DELTA,
            StreamEventType.REASONING_SUMMARY_DELTA,
        }:
            block_id = event_data.get("block_id")
            delta = event_data.get("delta")
            if (
                isinstance(block_id, str)
                and isinstance(delta, str)
                and block_id in active_reasoning_blocks
            ):
                active_reasoning_blocks[block_id]["text"] += delta
            return
        if event_type is StreamEventType.REASONING_COMPLETED:
            block = event_data.get("block")
            if isinstance(block, dict) and isinstance(block.get("block_id"), str):
                active_reasoning_blocks.pop(block["block_id"], None)

    async def _record_interrupted_reasoning_events(
        self,
        active_reasoning_blocks: dict[str, dict[str, str]],
        *,
        sequence: int,
        attempt_id: str,
        run_id: str,
        task_id: str | None,
        display_policy: ReasoningDisplayPolicy,
        started_at: datetime,
        first_visible_token_at: datetime | None,
    ) -> tuple[int, list[StreamEvent]]:
        """Persist authoritative partial blocks before emitting a failed stream terminal event."""

        interrupted_events: list[StreamEvent] = []
        for block_index, (block_id, active_block) in enumerate(
            active_reasoning_blocks.items()
        ):
            sequence += 1
            visible_text = active_block["text"] or None
            presentation_kind = active_block["kind"]
            if visible_text is None:
                presentation_kind = ReasoningPresentationKind.STATUS.value
            block = {
                "kind": presentation_kind,
                "block_id": block_id,
                "status": ReasoningBlockStatus.INTERRUPTED.value,
                "reasoning_tokens": None,
            }
            if visible_text is not None:
                block["text"] = visible_text
            interrupted_event = StreamEvent(
                type=StreamEventType.REASONING_INTERRUPTED,
                sequence=sequence,
                response_id=attempt_id,
                run_id=run_id,
                step_id=task_id,
                timestamp_ms=int(datetime.now(UTC).timestamp() * 1000),
                data={"attempt_id": attempt_id, "block": block},
            )
            await record_model_response_event(
                self.db_session,
                attempt_id=attempt_id,
                event=interrupted_event,
            )
            self.db_session.add(
                LlmReasoningBlock(
                    reasoning_block_id=block_id,
                    attempt_id=attempt_id,
                    block_index=block_index,
                    presentation_kind=presentation_kind,
                    status=ReasoningBlockStatus.INTERRUPTED.value,
                    visible_text=visible_text,
                    reasoning_tokens=None,
                    display_policy=display_policy.value,
                    final_event_sequence=sequence,
                    snapshot_version=1,
                    started_at=started_at,
                    first_visible_token_at=(
                        first_visible_token_at if visible_text else None
                    ),
                    completed_at=datetime.now(UTC),
                )
            )
            await self.db_session.commit()
            interrupted_events.append(interrupted_event)
        return sequence, interrupted_events

    @classmethod
    def _sanitize_raw_response_for_audit(cls, value: Any) -> Any:
        """Recursively remove Provider continuation material from ordinary audit objects."""

        if isinstance(value, list):
            return [cls._sanitize_raw_response_for_audit(item) for item in value]
        if isinstance(value, dict):
            return {
                key: cls._sanitize_raw_response_for_audit(item)
                for key, item in value.items()
                if key not in {"reasoning_content", "encrypted_content"}
            }
        return value

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
                reasoning_tokens=response.reasoning_tokens,
                estimated_cost=response.estimated_cost,
            ),
            reasoning_blocks=response.reasoning_blocks,
            latency_ms=response.latency_ms,
            provider_request_id=None,
        )
