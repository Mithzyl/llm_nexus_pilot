"""Evaluation and Evaluation rule set controllers."""

from fastapi import APIRouter, Depends, Response, status

from nexuspilot_api.core.security import require_internal_api_key
from nexuspilot_api.routers.common import DatabaseSessionDependency
from nexuspilot_api.schemas.evaluation import (
    EvaluationCreate,
    EvaluationRead,
    EvaluationRuleSetCreate,
    EvaluationRuleSetStatusUpdate,
)
from nexuspilot_api.services.evaluation_service import (
    create_evaluation,
    create_rule_set,
    get_evaluation,
    update_rule_set_status,
)

router = APIRouter(tags=["evaluations"])
internal_key = [Depends(require_internal_api_key)]


@router.post(
    "/evaluations",
    response_model=EvaluationRead,
    status_code=status.HTTP_201_CREATED,
)
async def post_evaluation(
    payload: EvaluationCreate,
    response: Response,
    db_session: DatabaseSessionDependency,
) -> EvaluationRead:
    """Run deterministic rules and persist one idempotent Evaluation result."""

    result = await create_evaluation(db_session, payload)
    if result.was_replayed:
        response.status_code = status.HTTP_200_OK
    return result.record


@router.get(
    "/evaluations/{evaluation_id}",
    response_model=EvaluationRead,
)
async def get_evaluation_by_id(
    evaluation_id: str,
    db_session: DatabaseSessionDependency,
) -> EvaluationRead:
    """Return one persisted Evaluation with its rule verdict evidence."""

    return await get_evaluation(db_session, evaluation_id)


@router.post(
    "/internal/evaluation-rule-sets",
    response_model=dict,
    status_code=status.HTTP_201_CREATED,
    dependencies=internal_key,
)
async def post_internal_evaluation_rule_set(
    payload: EvaluationRuleSetCreate,
    db_session: DatabaseSessionDependency,
) -> dict:
    """Create one versioned rule set behind both API-key boundaries."""

    return await create_rule_set(db_session, payload)


@router.patch(
    "/internal/evaluation-rule-sets/{rule_set_id}/status",
    response_model=dict,
    dependencies=internal_key,
)
async def patch_internal_evaluation_rule_set_status(
    rule_set_id: str,
    payload: EvaluationRuleSetStatusUpdate,
    db_session: DatabaseSessionDependency,
) -> dict:
    """Enable or disable one rule set version."""

    return await update_rule_set_status(db_session, rule_set_id, payload)
