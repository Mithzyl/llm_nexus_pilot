"""Reusable HTTP, retry, and SSE transport without provider payload knowledge."""

import asyncio
import json
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import httpx

from nexuspilot_models.contracts import TransportAttempt
from nexuspilot_models.errors import ModelProviderError
from nexuspilot_models.diagnostics import provider_error_diagnostic


@dataclass(frozen=True)
class JsonHttpResult:
    """Return decoded JSON, response headers, and all physical transport attempts."""

    payload: dict[str, Any]
    headers: httpx.Headers
    attempts: list[TransportAttempt]


@dataclass(frozen=True)
class SSEMessage:
    """Represent one decoded Server-Sent Event before provider normalization."""

    event: str | None
    data: str
    event_id: str | None = None


class HttpTransport:
    """Execute JSON and SSE requests with bounded retries for temporary failures."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        max_retries: int = 2,
        backoff_seconds: float = 0.1,
    ) -> None:
        """Configure a shared client and retry limits without owning client shutdown."""

        self.client = client
        self.max_retries = max_retries
        self.backoff_seconds = backoff_seconds

    @staticmethod
    def _request_timeout(timeout_seconds: float | None) -> httpx.Timeout:
        """Remove only the model read deadline; keep connection, write and pool waits bounded."""
        if timeout_seconds is None:
            return httpx.Timeout(connect=10, read=None, write=30, pool=30)
        return httpx.Timeout(timeout_seconds)

    async def post_json(
        self,
        url: str,
        *,
        headers: dict[str, str],
        payload: dict[str, Any],
        timeout_seconds: float | None,
    ) -> JsonHttpResult:
        """POST JSON with optional unlimited read time; cancellation never triggers a retry."""

        attempts: list[TransportAttempt] = []
        for attempt_index in range(1, self.max_retries + 2):
            started = time.perf_counter()
            try:
                response = await self.client.post(
                    url,
                    headers=headers,
                    json=payload,
                    timeout=self._request_timeout(timeout_seconds),
                )
            except httpx.TimeoutException as exc:
                attempts.append(
                    self._network_attempt(attempt_index, started, "timeout", exc)
                )
                if attempt_index <= self.max_retries:
                    await self._backoff(attempt_index)
                    continue
                raise ModelProviderError(
                    "timeout",
                    "Model provider request timed out.",
                    retryable=True,
                    transport_attempts=attempts,
                ) from exc
            except httpx.RequestError as exc:
                attempts.append(
                    self._network_attempt(attempt_index, started, "network_error", exc)
                )
                if attempt_index <= self.max_retries:
                    await self._backoff(attempt_index)
                    continue
                raise ModelProviderError(
                    "network_error",
                    "Model provider network request failed.",
                    retryable=True,
                    transport_attempts=attempts,
                ) from exc

            latency_ms = self._elapsed_ms(started)
            if response.is_success:
                attempts.append(
                    TransportAttempt(
                        attempt_index=attempt_index,
                        status_code=response.status_code,
                        latency_ms=latency_ms,
                    )
                )
                return JsonHttpResult(
                    payload=self._decode_json(response, attempts),
                    headers=response.headers,
                    attempts=attempts,
                )

            error_type, retryable = self._classify_status(response.status_code)
            safe_message = self._safe_error_message(response)
            attempts.append(
                TransportAttempt(
                    attempt_index=attempt_index,
                    status_code=response.status_code,
                    latency_ms=latency_ms,
                    error_type=error_type,
                    error_message=safe_message,
                )
            )
            if retryable and attempt_index <= self.max_retries:
                await self._backoff(attempt_index)
                continue
            raise ModelProviderError(
                error_type,
                safe_message,
                retryable=retryable,
                status_code=response.status_code,
                raw_error=self._safe_json(response),
                transport_attempts=attempts,
            )

        raise AssertionError("retry loop must return or raise")

    @asynccontextmanager
    async def open_sse(
        self,
        url: str,
        *,
        headers: dict[str, str],
        payload: dict[str, Any],
        timeout_seconds: float | None,
    ) -> AsyncIterator[
        tuple[AsyncIterator[SSEMessage], httpx.Headers, list[TransportAttempt]]
    ]:
        """Allow unlimited stream reads on request while closing transport on cancellation."""

        attempts: list[TransportAttempt] = []
        for attempt_index in range(1, self.max_retries + 2):
            started = time.perf_counter()
            try:
                stream_context = self.client.stream(
                    "POST",
                    url,
                    headers=headers,
                    json=payload,
                    timeout=self._request_timeout(timeout_seconds),
                )
                response = await stream_context.__aenter__()
            except httpx.TimeoutException as exc:
                attempts.append(
                    self._network_attempt(attempt_index, started, "timeout", exc)
                )
                if attempt_index <= self.max_retries:
                    await self._backoff(attempt_index)
                    continue
                raise ModelProviderError(
                    "timeout",
                    "Model provider stream connection timed out.",
                    retryable=True,
                    transport_attempts=attempts,
                ) from exc
            except httpx.RequestError as exc:
                attempts.append(
                    self._network_attempt(attempt_index, started, "network_error", exc)
                )
                if attempt_index <= self.max_retries:
                    await self._backoff(attempt_index)
                    continue
                raise ModelProviderError(
                    "network_error",
                    "Model provider stream connection failed.",
                    retryable=True,
                    transport_attempts=attempts,
                ) from exc

            latency_ms = self._elapsed_ms(started)
            if response.is_success:
                attempts.append(
                    TransportAttempt(
                        attempt_index=attempt_index,
                        status_code=response.status_code,
                        latency_ms=latency_ms,
                    )
                )
                try:
                    yield (
                        self._iter_sse(response.aiter_lines()),
                        response.headers,
                        attempts,
                    )
                except (httpx.TimeoutException, httpx.RequestError) as exc:
                    error_type = (
                        "timeout"
                        if isinstance(exc, httpx.TimeoutException)
                        else "network_error"
                    )
                    attempts[-1].error_type = error_type
                    attempts[-1].error_message = type(exc).__name__
                    attempts[-1].latency_ms = self._elapsed_ms(started)
                    # Some output may already be delivered: never restart the generation here.
                    raise ModelProviderError(
                        error_type,
                        "Model provider stream read failed.",
                        transport_attempts=attempts,
                    ) from exc
                finally:
                    await stream_context.__aexit__(None, None, None)
                return

            body = await response.aread()
            await stream_context.__aexit__(None, None, None)
            error_type, retryable = self._classify_status(response.status_code)
            message = self._safe_error_message_from_bytes(body)
            raw_error = self._error_diagnostic_from_bytes(body, response.status_code)
            attempts.append(
                TransportAttempt(
                    attempt_index=attempt_index,
                    status_code=response.status_code,
                    latency_ms=latency_ms,
                    error_type=error_type,
                    error_message=message,
                )
            )
            if retryable and attempt_index <= self.max_retries:
                await self._backoff(attempt_index)
                continue
            raise ModelProviderError(
                error_type,
                message,
                retryable=retryable,
                status_code=response.status_code,
                raw_error=raw_error,
                transport_attempts=attempts,
            )

        raise AssertionError("retry loop must yield or raise")

    async def _iter_sse(self, lines: AsyncIterator[str]) -> AsyncIterator[SSEMessage]:
        """Parse SSE comments, names, IDs, and multiline data without provider assumptions."""

        event: str | None = None
        event_id: str | None = None
        data_lines: list[str] = []
        async for line in lines:
            if line == "":
                if data_lines:
                    yield SSEMessage(
                        event=event, event_id=event_id, data="\n".join(data_lines)
                    )
                event = None
                data_lines = []
                continue
            if line.startswith(":"):
                continue
            field, _, value = line.partition(":")
            value = value[1:] if value.startswith(" ") else value
            if field == "event":
                event = value
            elif field == "id":
                event_id = value
            elif field == "data":
                data_lines.append(value)
        if data_lines:
            yield SSEMessage(event=event, event_id=event_id, data="\n".join(data_lines))

    async def _backoff(self, attempt_index: int) -> None:
        """Wait using bounded exponential backoff before another physical request."""

        await asyncio.sleep(self.backoff_seconds * (2 ** (attempt_index - 1)))

    def _network_attempt(
        self,
        attempt_index: int,
        started: float,
        error_type: str,
        error: Exception,
    ) -> TransportAttempt:
        """Build a safe trace entry for a network exception without request credentials."""

        return TransportAttempt(
            attempt_index=attempt_index,
            latency_ms=self._elapsed_ms(started),
            error_type=error_type,
            error_message=type(error).__name__,
        )

    def _decode_json(
        self, response: httpx.Response, attempts: list[TransportAttempt]
    ) -> dict[str, Any]:
        """Decode a JSON object or raise a stable parse failure with transport evidence."""

        try:
            payload = response.json()
        except (json.JSONDecodeError, ValueError) as exc:
            raise ModelProviderError(
                "response_parse_error",
                "Model provider returned invalid JSON.",
                status_code=response.status_code,
                transport_attempts=attempts,
            ) from exc
        if not isinstance(payload, dict):
            raise ModelProviderError(
                "response_parse_error",
                "Model provider returned a non-object JSON response.",
                status_code=response.status_code,
                transport_attempts=attempts,
            )
        return payload

    def _safe_json(self, response: httpx.Response) -> dict[str, Any]:
        """Retain allowlisted diagnostics, never raw provider request echoes."""

        return self._error_diagnostic_from_bytes(response.content, response.status_code)

    def _error_diagnostic_from_bytes(
        self, body: bytes, status_code: int
    ) -> dict[str, Any]:
        """Classify JSON or non-JSON errors for private audit without retaining free text."""

        try:
            payload = json.loads(body)
        except (ValueError, UnicodeDecodeError):
            payload = body.decode("utf-8", errors="replace")
        return provider_error_diagnostic(payload, http_status=status_code)

    def _safe_error_message(self, response: httpx.Response) -> str:
        """Keep the public HTTP error stable without exposing arbitrary upstream text."""

        return f"Model provider returned HTTP {response.status_code}."

    def _safe_error_message_from_bytes(self, body: bytes) -> str:
        """Never expose a rejected stream's arbitrary error body as a public message."""

        del body
        return "Model provider rejected the stream request."

    def _classify_status(self, status_code: int) -> tuple[str, bool]:
        """Map HTTP status codes to the platform error taxonomy and retry policy."""

        if status_code in {401, 403}:
            return "authentication_error", False
        if status_code == 429:
            return "rate_limited", True
        if status_code in {408, 504}:
            return "timeout", True
        if status_code >= 500:
            return "provider_unavailable", True
        return "invalid_request", False

    def _elapsed_ms(self, started: float) -> int:
        """Return non-negative elapsed milliseconds from a monotonic start time."""

        return max(0, int((time.perf_counter() - started) * 1000))
