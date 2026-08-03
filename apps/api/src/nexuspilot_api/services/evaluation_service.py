"""Deterministic Evaluation and rule set management use cases."""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from nexuspilot_api.core.errors import (
    ResourceConflictError,
    ResourceNotFoundError,
)
from nexuspilot_api.features.memory.services.credential_guard import (
    CREDENTIAL_GUARD_VERSION,
    scan_for_sensitive_input,
)
from nexuspilot_api.features.memory.services.memory_policy import hash_memory_request
from nexuspilot_api.models import (
    EvaluationRuleSetStatus,
    EvaluationStatus,
    EvaluationVerdict,
    LlmEvaluationRule,
    LlmEvaluationRuleSet,
    LlmModelAttempt,
    LlmRun,
    LlmTask,
    LlmTaskEvaluation,
    new_id,
)
from nexuspilot_api.schemas.evaluation import (
    EvaluationCreate,
    EvaluationRead,
    EvaluationRuleFindingRead,
    EvaluationRuleSetCreate,
    EvaluationRuleSetStatusUpdate,
)

MAX_EVALUATION_INPUT_CHARACTERS = 40_000


@dataclass(frozen=True)
class EvaluationWriteResult:
    """Contain one persisted Evaluation and whether an idempotent request replayed."""

    record: EvaluationRead
    was_replayed: bool


async def create_rule_set(
    db_session: AsyncSession,
    payload: EvaluationRuleSetCreate,
) -> dict:
    """Persist one versioned rule set with immutable deterministic rules."""

    rule_set = LlmEvaluationRuleSet(
        rule_set_id=new_id(),
        name=payload.name,
        schema_version=payload.schema_version,
        description=payload.description,
        status=EvaluationRuleSetStatus.ENABLED,
        rule_count=len(payload.rules),
    )
    db_session.add(rule_set)
    await db_session.flush()
    db_session.add_all(
        [
            LlmEvaluationRule(
                rule_id=new_id(),
                rule_set_id=rule_set.rule_set_id,
                rule_key=rule.rule_key,
                rule_type=rule.rule_type,
                severity=rule.severity,
                config_json=rule.config_json,
            )
            for rule in payload.rules
        ]
    )
    try:
        await db_session.commit()
    except IntegrityError as exc:
        await db_session.rollback()
        raise ResourceConflictError(
            "Evaluation rule set version already exists"
        ) from exc
    return {
        "rule_set_id": rule_set.rule_set_id,
        "name": rule_set.name,
        "schema_version": rule_set.schema_version,
        "status": rule_set.status,
        "rule_count": rule_set.rule_count,
    }


async def update_rule_set_status(
    db_session: AsyncSession,
    rule_set_id: str,
    payload: EvaluationRuleSetStatusUpdate,
) -> dict:
    """Enable or disable one rule set version."""

    rule_set = await db_session.get(LlmEvaluationRuleSet, rule_set_id)
    if rule_set is None:
        raise ResourceNotFoundError("Evaluation Rule Set")
    rule_set.status = payload.status
    await db_session.commit()
    await db_session.refresh(rule_set)
    return {
        "rule_set_id": rule_set.rule_set_id,
        "name": rule_set.name,
        "schema_version": rule_set.schema_version,
        "status": rule_set.status,
        "rule_count": rule_set.rule_count,
    }


async def create_evaluation(
    db_session: AsyncSession,
    payload: EvaluationCreate,
) -> EvaluationWriteResult:
    """Run deterministic rules and persist one idempotent Evaluation result."""

    run = await db_session.get(LlmRun, payload.run_id)
    if run is None:
        raise ResourceNotFoundError("Run")
    task = await db_session.get(LlmTask, payload.task_id)
    if task is None:
        raise ResourceNotFoundError("Task")
    if task.run_id != run.run_id:
        raise ResourceConflictError("Task does not belong to the Run")
    if payload.candidate_attempt_id is not None:
        attempt = await db_session.get(LlmModelAttempt, payload.candidate_attempt_id)
        if attempt is None:
            raise ResourceNotFoundError("Model Attempt")
        if attempt.run_id != run.run_id:
            raise ResourceConflictError("Candidate attempt does not belong to the Run")
    rule_set = await db_session.get(LlmEvaluationRuleSet, payload.rule_set_id)
    if rule_set is None:
        raise ResourceNotFoundError("Evaluation Rule Set")
    if rule_set.status != EvaluationRuleSetStatus.ENABLED:
        raise ResourceConflictError("Evaluation rule set is disabled")

    request_hash = hash_memory_request(payload.model_dump(mode="json"))
    replay = await db_session.scalar(
        select(LlmTaskEvaluation).where(
            LlmTaskEvaluation.task_id == payload.task_id,
            LlmTaskEvaluation.idempotency_key == payload.idempotency_key,
        )
    )
    if replay is not None:
        return EvaluationWriteResult(
            record=await get_evaluation(db_session, replay.evaluation_id),
            was_replayed=True,
        )
    rules = list(
        (
            await db_session.scalars(
                select(LlmEvaluationRule).where(
                    LlmEvaluationRule.rule_set_id == rule_set.rule_set_id
                )
            )
        ).all()
    )
    findings = _run_deterministic_rules(rules, payload)
    overall_failed = any(
        finding.verdict == EvaluationVerdict.FAIL
        and finding.severity == "error"
        for finding in findings
    )
    verdict = EvaluationVerdict.FAIL if overall_failed else EvaluationVerdict.PASS
    if findings and not overall_failed:
        verdict = EvaluationVerdict.PASS
    evaluation = LlmTaskEvaluation(
        evaluation_id=new_id(),
        run_id=run.run_id,
        task_id=task.task_id,
        candidate_attempt_id=payload.candidate_attempt_id,
        evaluation_type=payload.evaluation_type,
        rule_set_id=rule_set.rule_set_id,
        rule_set_schema_version=rule_set.schema_version,
        idempotency_key=payload.idempotency_key,
        input_evidence_uri=payload.input_evidence_uri,
        status=EvaluationStatus.COMPLETED,
        verdict=verdict,
        error_code=None,
        findings_json={
            "request_hash": request_hash,
            "findings": [
                {
                    "rule_key": finding.rule_key,
                    "rule_type": finding.rule_type,
                    "severity": finding.severity,
                    "verdict": finding.verdict.value,
                    "evidence": finding.evidence,
                }
                for finding in findings
            ],
        },
        completed_at=None,
    )
    from nexuspilot_api.models.base import utc_now

    evaluation.completed_at = utc_now()
    db_session.add(evaluation)
    try:
        await db_session.commit()
    except IntegrityError as exc:
        await db_session.rollback()
        replay = await db_session.scalar(
            select(LlmTaskEvaluation).where(
                LlmTaskEvaluation.task_id == payload.task_id,
                LlmTaskEvaluation.idempotency_key == payload.idempotency_key,
            )
        )
        if replay is not None:
            return EvaluationWriteResult(
                record=await get_evaluation(db_session, replay.evaluation_id),
                was_replayed=True,
            )
        raise ResourceConflictError("Evaluation idempotency conflict") from exc
    return EvaluationWriteResult(
        record=await get_evaluation(db_session, evaluation.evaluation_id),
        was_replayed=False,
    )


async def get_evaluation(
    db_session: AsyncSession,
    evaluation_id: str,
) -> EvaluationRead:
    """Return one persisted Evaluation with rule verdict evidence."""

    evaluation = await db_session.get(LlmTaskEvaluation, evaluation_id)
    if evaluation is None:
        raise ResourceNotFoundError("Evaluation")
    findings = [
        EvaluationRuleFindingRead.model_validate(finding)
        for finding in evaluation.findings_json.get("findings", [])
    ]
    return EvaluationRead(
        evaluation_id=evaluation.evaluation_id,
        run_id=evaluation.run_id,
        task_id=evaluation.task_id,
        candidate_attempt_id=evaluation.candidate_attempt_id,
        evaluator_attempt_id=evaluation.evaluator_attempt_id,
        evaluation_type=evaluation.evaluation_type,
        rule_set_id=evaluation.rule_set_id,
        rule_set_schema_version=evaluation.rule_set_schema_version,
        status=evaluation.status,
        verdict=evaluation.verdict,
        score=evaluation.score,
        error_code=evaluation.error_code,
        findings=findings,
        created_at=evaluation.created_at,
        completed_at=evaluation.completed_at,
    )


def _run_deterministic_rules(
    rules: list[LlmEvaluationRule],
    payload: EvaluationCreate,
) -> list[EvaluationRuleFindingRead]:
    """Evaluate every rule and keep stable evidence without copying sensitive input."""

    findings: list[EvaluationRuleFindingRead] = []
    for rule in rules:
        verdict, evidence = _evaluate_rule(rule, payload)
        findings.append(
            EvaluationRuleFindingRead(
                rule_key=rule.rule_key,
                rule_type=rule.rule_type,
                severity=rule.severity,
                verdict=verdict,
                evidence=evidence,
            )
        )
    return findings


def _evaluate_rule(
    rule: LlmEvaluationRule,
    payload: EvaluationCreate,
) -> tuple[EvaluationVerdict, str]:
    """Evaluate one deterministic rule and never echo rejected sensitive input."""

    config = rule.config_json or {}
    if rule.rule_type == "input_length":
        maximum = int(config.get("max_characters", MAX_EVALUATION_INPUT_CHARACTERS))
        length = len(payload.input_text or "")
        if length > maximum:
            return EvaluationVerdict.FAIL, f"input_length {length} exceeds {maximum}"
        return EvaluationVerdict.PASS, f"input_length {length} within {maximum}"
    if rule.rule_type == "credential_scan":
        matched = scan_for_sensitive_input(payload.input_text or "")
        if matched:
            return (
                EvaluationVerdict.FAIL,
                f"{CREDENTIAL_GUARD_VERSION} matched {','.join(sorted(matched))}",
            )
        return EvaluationVerdict.PASS, f"{CREDENTIAL_GUARD_VERSION} clean"
    if rule.rule_type == "attempt_reference":
        if payload.candidate_attempt_id is None:
            return EvaluationVerdict.FAIL, "candidate_attempt_id required"
        return EvaluationVerdict.PASS, "candidate attempt present"
    if rule.rule_type == "evidence_reference":
        if not payload.input_evidence_uri:
            return EvaluationVerdict.FAIL, "input_evidence_uri required"
        return EvaluationVerdict.PASS, "input evidence present"
    return EvaluationVerdict.PASS, "unsupported rule treated as pass"
