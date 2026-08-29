"""Redacted schemas for the Phase 1 internal audit and operations boundary."""

from datetime import datetime
from decimal import Decimal
from typing import Any

from nexuspilot_api.schemas.base import ApiModel


class ModelToolCallSummary(ApiModel):
    """Expose bounded metadata for one tool call made by an LLM model invocation."""

    tool_call_id: str
    attempt_id: str
    tool_name: str
    risk_level: str
    status: str
    permission_decision: str | None
    has_result: bool
    started_at: datetime
    completed_at: datetime | None


class ModelToolCallDetail(ModelToolCallSummary):
    """Expose redacted tool input and a bounded failure preview for internal audit."""

    input_json: dict[str, Any]
    error_message_preview: str | None


class TaskEvaluationSummary(ApiModel):
    """Expose bounded metadata for one deterministic or model-based Task evaluation."""

    evaluation_id: str
    run_id: str
    task_id: str
    candidate_attempt_id: str | None
    evaluator_attempt_id: str | None
    evaluation_type: str
    verdict: str
    score: Decimal | None
    created_at: datetime


class TaskEvaluationDetail(TaskEvaluationSummary):
    """Expose redacted findings for one internal Task evaluation record."""

    findings_json: dict[str, Any]


class OutboxEventSummary(ApiModel):
    """Expose delivery-state metadata for one infrastructure outbox event."""

    event_id: str
    aggregate_type: str
    aggregate_id: str
    event_type: str
    status: str
    publish_attempts: int
    next_retry_at: datetime | None
    published_at: datetime | None
    created_at: datetime


class OutboxEventDetail(OutboxEventSummary):
    """Expose one redacted infrastructure message payload for internal operations."""

    payload_json: dict[str, Any]


class ModelCacheDiagnosticGroup(ApiModel):
    """Expose cache observations for one stable Provider, model, role, and prompt prefix."""

    provider: str
    model: str
    agent_role: str | None
    prompt_prefix_fingerprint: str | None
    request_count: int
    requests_reporting_cache_usage: int
    total_input_tokens: int
    total_cached_tokens: int
    total_cache_write_tokens: int
    total_cache_miss_tokens: int
    first_observed_cached_tokens: int | None
    first_observed_latency_ms: int | None
    first_observed_first_visible_token_latency_ms: int | None
    subsequent_request_count: int
    subsequent_input_tokens: int
    subsequent_cached_tokens: int
    subsequent_cache_hit_ratio: Decimal | None
    subsequent_average_latency_ms: Decimal | None
    subsequent_average_first_visible_token_latency_ms: Decimal | None


class ModelCacheDiagnosticsRead(ApiModel):
    """Return bounded run-scoped cache diagnostics without prompt or response content."""

    run_id: str
    workflow_name: str | None
    workflow_version: str | None
    execution_profile: str | None
    attempt_count: int
    groups: list[ModelCacheDiagnosticGroup]
