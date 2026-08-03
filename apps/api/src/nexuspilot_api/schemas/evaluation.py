"""Evaluation rule sets and deterministic Evaluation HTTP schemas."""

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from nexuspilot_api.models import (
    EvaluationRuleSetStatus,
    EvaluationStatus,
    EvaluationVerdict,
)
from nexuspilot_api.schemas.base import ApiModel


class EvaluationRuleCreate(BaseModel):
    """Validate one immutable deterministic rule definition."""

    model_config = ConfigDict(extra="forbid")

    rule_key: str = Field(min_length=1, max_length=128)
    rule_type: str = Field(min_length=1, max_length=64)
    severity: str = Field(default="error", min_length=1, max_length=32)
    config_json: dict = Field(default_factory=dict)


class EvaluationRuleSetCreate(BaseModel):
    """Validate one versioned rule set with at least one deterministic rule."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=128)
    schema_version: str = Field(min_length=1, max_length=64)
    description: str | None = Field(default=None, max_length=4_000)
    rules: list[EvaluationRuleCreate] = Field(min_length=1, max_length=50)


class EvaluationRuleSetStatusUpdate(BaseModel):
    """Validate one explicit rule set enablement transition."""

    model_config = ConfigDict(extra="forbid")

    status: EvaluationRuleSetStatus


class EvaluationCreate(BaseModel):
    """Validate one idempotent deterministic Evaluation of a task result."""

    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(min_length=1, max_length=36)
    task_id: str = Field(min_length=1, max_length=36)
    candidate_attempt_id: str | None = Field(default=None, min_length=1, max_length=36)
    evaluation_type: str = Field(min_length=1, max_length=64)
    rule_set_id: str = Field(min_length=1, max_length=36)
    idempotency_key: str = Field(min_length=1, max_length=128)
    input_evidence_uri: str | None = Field(default=None, max_length=1024)
    input_text: str | None = Field(default=None, max_length=40_000)


class EvaluationRuleFindingRead(ApiModel):
    """Expose one rule verdict with stable evidence and no copied sensitive text."""

    rule_key: str
    rule_type: str
    severity: str
    verdict: EvaluationVerdict
    evidence: str


class EvaluationRead(ApiModel):
    """Expose one Evaluation with execution status kept separate from its verdict."""

    evaluation_id: str
    run_id: str
    task_id: str
    candidate_attempt_id: str | None
    evaluator_attempt_id: str | None
    evaluation_type: str
    rule_set_id: str | None
    rule_set_schema_version: str | None
    status: EvaluationStatus
    verdict: EvaluationVerdict
    score: Decimal | None
    error_code: str | None
    findings: list[EvaluationRuleFindingRead]
    created_at: datetime
    completed_at: datetime | None
