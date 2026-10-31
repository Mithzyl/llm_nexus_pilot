"""Owned human replies; model tools cannot call these authenticated user controllers."""

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from nexuspilot_api.routers.common import DatabaseSessionDependency, ObjectStorageDependency
from nexuspilot_api.services.user_interactions import list_interactions, reply_to_interaction

router = APIRouter(tags=["user-interactions"])


class InteractionReply(BaseModel):
    """Validate a bounded versioned answer with no client-controlled owner or execution state."""

    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=1)
    key: str = Field(min_length=8, max_length=128)
    approved: StrictBool | None = None
    text: str | None = Field(default=None, min_length=1, max_length=4000)


@router.get("/runs/{run_id}/interactions")
async def get_interactions(
    run_id: str, db: DatabaseSessionDependency, storage: ObjectStorageDependency
):
    """Restore current questions and decisions for the authenticated Run owner."""
    return {
        "items": await list_interactions(db, storage, db.info.get("authorized_user_id"), run_id)
    }


@router.post("/runs/{run_id}/interactions/{interaction_id}/reply")
async def post_interaction_reply(
    run_id: str,
    interaction_id: str,
    payload: InteractionReply,
    db: DatabaseSessionDependency,
    storage: ObjectStorageDependency,
):
    """Persist a human reply once; repeated identical submission returns the original decision."""
    return await reply_to_interaction(
        db,
        storage,
        db.info.get("authorized_user_id"),
        run_id,
        interaction_id,
        payload.model_dump(exclude_none=True),
    )
