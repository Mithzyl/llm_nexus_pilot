"""Internal-only publication of application prompt scenes; browser users cannot choose templates."""

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from nexuspilot_api.core.errors import InvalidRequestError, ResourceConflictError
from nexuspilot_api.models.enums import PromptTemplateStatus
from nexuspilot_api.models.prompt_catalog import LlmPromptTemplate, LlmPromptTemplateVersion
from nexuspilot_api.models.prompt_scenes import PromptSceneBinding
from nexuspilot_api.routers.common import DatabaseSessionDependency

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
    """Bind an enabled static template with version checks; old Runs keep their selection."""
    version = await db.get(LlmPromptTemplateVersion, payload.template_version_id)
    template = await db.get(LlmPromptTemplate, version.template_name) if version else None
    if template is None or template.status != PromptTemplateStatus.ENABLED:
        raise InvalidRequestError("Scene requires an enabled template version")
    if version.variable_schema_json:
        raise InvalidRequestError("Scene templates must be static")
    row = await db.scalar(
        select(PromptSceneBinding)
        .where(
            PromptSceneBinding.entry == payload.entry,
            PromptSceneBinding.scene_id == payload.scene_id,
            PromptSceneBinding.execution_mode == payload.execution_mode,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is None:
        if payload.expected_version != 0:
            raise ResourceConflictError("Scene version changed")
        row = PromptSceneBinding(
            entry=payload.entry,
            scene_id=payload.scene_id,
            execution_mode=payload.execution_mode,
            version=1,
        )
        db.add(row)
    elif row.template_version_id == payload.template_version_id and row.enabled == payload.enabled:
        return {"binding_id": row.binding_id, "version": row.version}
    elif row.version != payload.expected_version:
        raise ResourceConflictError("Scene version changed")
    else:
        row.version += 1
    row.template_version_id, row.enabled = payload.template_version_id, payload.enabled
    await db.commit()
    return {"binding_id": row.binding_id, "version": row.version}
