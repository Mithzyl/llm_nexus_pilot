"""Select approved static scene templates; caller text cannot select another application scope."""

import json

from sqlalchemy import select

from nexuspilot_api.core.errors import InvalidRequestError
from nexuspilot_api.models.enums import PromptTemplateStatus
from nexuspilot_api.models.prompt_catalog import LlmPromptTemplate, LlmPromptTemplateVersion
from nexuspilot_api.models.prompt_scenes import PromptSceneBinding, RunPromptSceneSelection


async def pin_scene(db, run, entry, scene_id):
    """Pin published mode-specific references or absence before any model invocation."""
    rows = (
        await db.execute(
            select(PromptSceneBinding, LlmPromptTemplateVersion, LlmPromptTemplate)
            .join(
                LlmPromptTemplateVersion,
                PromptSceneBinding.template_version_id
                == LlmPromptTemplateVersion.template_version_id,
            )
            .join(
                LlmPromptTemplate,
                LlmPromptTemplate.template_name == LlmPromptTemplateVersion.template_name,
            )
            .where(
                PromptSceneBinding.entry == entry,
                PromptSceneBinding.scene_id == (scene_id or "default"),
                PromptSceneBinding.enabled.is_(True),
                LlmPromptTemplate.status == PromptTemplateStatus.ENABLED,
            )
        )
    ).all()
    if scene_id and not rows:
        raise InvalidRequestError("Scene is not approved for this application")
    selected = []
    for binding, version, _template in rows:
        if version.variable_schema_json:
            raise InvalidRequestError("Scene template must not require runtime variables")
        selected.append(
            {
                "binding_id": binding.binding_id,
                "binding_version": binding.version,
                "mode": binding.execution_mode,
                "template_version_id": version.template_version_id,
            }
        )
    db.add(
        RunPromptSceneSelection(
            run_id=run.run_id,
            entry=entry,
            explicit_scene=scene_id is not None,
            selections_json=selected,
        )
    )


async def apply_scene(db, payload):
    """Prepend the pinned scene to role instructions; runtime Context adds platform policy first."""
    selection = await db.get(RunPromptSceneSelection, payload.run_id)
    if selection is None:
        return payload
    mode = "agent" if payload.metadata.get("agent_role") else "quick"
    choices = [value for value in selection.selections_json if value["mode"] in {mode, "any"}]
    choices.sort(key=lambda value: value["mode"] != mode)
    if not choices:
        if selection.explicit_scene:
            raise InvalidRequestError("Scene is not approved for this execution mode")
        return payload
    reference = choices[0]
    version = await db.get(LlmPromptTemplateVersion, reference["template_version_id"])
    if version is None:
        raise InvalidRequestError("Selected scene version is unavailable")
    return payload.model_copy(
        update={
            "instructions": "\n\n".join(filter(None, [version.content_text, payload.instructions])),
            "metadata": {**payload.metadata, "prompt_scene_version": json.dumps(reference)},
        }
    )
