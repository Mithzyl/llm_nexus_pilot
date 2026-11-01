"""Select approved static scene templates; caller text cannot select another application scope."""

import json

from sqlalchemy import select

from nexuspilot_api.core.errors import InvalidRequestError, ResourceConflictError
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


async def publish_scene_binding(db, payload):
    """Publish an enabled static template with locked version checks and commit the binding."""
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
