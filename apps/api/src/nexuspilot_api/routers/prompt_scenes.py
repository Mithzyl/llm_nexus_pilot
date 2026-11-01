"""Internal-only publication of application prompt scenes; browser users cannot choose templates."""

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from nexuspilot_api.routers.common import DatabaseSessionDependency
from nexuspilot_api.services.prompt_scene_service import publish_scene_binding

router = APIRouter(tags=["internal-prompt-scenes"])


class SceneCommand(BaseModel):
    """Explicit versioned binding; credentials and user instructions are never accepted here."""

    model_config = ConfigDict(extra="forbid")
    entry: Literal["native", "personal_assistant", "retail"]
    scene_id: str = Field(min_length=1, max_length=64)
    execution_mode: Literal["any", "quick", "agent"] = "any"
    template_version_id: str = Field(min_length=36, max_length=36)
    enabled: bool = True
    expected_version: int = Field(ge=0)


@router.post("/internal/prompt-scenes")
async def publish_scene(payload: SceneCommand, db: DatabaseSessionDependency):
    """Delegate static scene publication while preserving Run-pinned selections."""
    return await publish_scene_binding(db, payload)
