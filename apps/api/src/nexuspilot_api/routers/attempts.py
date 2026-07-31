"""Model-attempt resource controller."""

from fastapi import APIRouter, status

from nexuspilot_api.routers.common import DatabaseSessionDependency
from nexuspilot_api.schemas.attempts import AttemptCreate, AttemptRead
from nexuspilot_api.services.attempt_service import create_attempt

router = APIRouter(tags=["attempts"])


@router.post(
    "/runs/{run_id}/attempts",
    response_model=AttemptRead,
    status_code=status.HTTP_201_CREATED,
)
async def post_attempt(
    run_id: str,
    payload: AttemptCreate,
    db_session: DatabaseSessionDependency,
) -> AttemptRead:
    """Persist an externally completed model call and update run-level estimated cost."""

    return AttemptRead.model_validate(await create_attempt(db_session, run_id, payload))
