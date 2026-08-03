"""Prompt render, template management, and Model Catalog controllers."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from nexuspilot_api.core.security import require_internal_api_key
from nexuspilot_api.routers.common import DatabaseSessionDependency
from nexuspilot_api.schemas.prompt_catalog import (
    ModelCapabilityRead,
    ModelCatalogStatusUpdate,
    ModelCatalogVersionCreate,
    PromptActiveVersionUpdate,
    PromptRenderCreate,
    PromptRenderRead,
    PromptTemplateCreate,
    PromptTemplateRead,
    PromptTemplateStatusUpdate,
    PromptTemplateVersionCreate,
)
from nexuspilot_api.services.prompt_catalog_service import (
    create_model_catalog_version,
    create_prompt_template,
    create_prompt_template_version,
    get_model_capability,
    render_prompt,
    set_active_prompt_version,
    update_model_catalog_status,
    update_prompt_template_status,
)

router = APIRouter(tags=["prompt-catalog"])
internal_key = [Depends(require_internal_api_key)]


@router.post("/prompt-renders", response_model=PromptRenderRead)
async def post_prompt_render(
    payload: PromptRenderCreate,
    db_session: DatabaseSessionDependency,
) -> PromptRenderRead:
    """Render one enabled Prompt version without producing any model cost."""

    return await render_prompt(db_session, payload)


@router.get("/model-capabilities", response_model=ModelCapabilityRead)
async def get_model_capabilities(
    provider: Annotated[str, Query(min_length=1, max_length=64)],
    model: Annotated[str, Query(min_length=1, max_length=128)],
    db_session: DatabaseSessionDependency,
    catalog_version: Annotated[str | None, Query(max_length=64)] = None,
) -> ModelCapabilityRead:
    """Return explicit support, unsupported, or unknown capability answers."""

    return await get_model_capability(db_session, provider, model, catalog_version)


@router.post(
    "/internal/prompt-templates",
    response_model=PromptTemplateRead,
    dependencies=internal_key,
)
async def post_internal_prompt_template(
    payload: PromptTemplateCreate,
    db_session: DatabaseSessionDependency,
) -> PromptTemplateRead:
    """Create one Prompt template behind both API-key boundaries."""

    return await create_prompt_template(db_session, payload)


@router.post(
    "/internal/prompt-templates/{template_name}/versions",
    response_model=PromptTemplateRead,
    dependencies=internal_key,
)
async def post_internal_prompt_template_version(
    template_name: str,
    payload: PromptTemplateVersionCreate,
    db_session: DatabaseSessionDependency,
) -> PromptTemplateRead:
    """Append and activate one immutable Prompt version."""

    return await create_prompt_template_version(db_session, template_name, payload)


@router.patch(
    "/internal/prompt-templates/{template_name}/active-version",
    response_model=PromptTemplateRead,
    dependencies=internal_key,
)
async def patch_internal_prompt_active_version(
    template_name: str,
    payload: PromptActiveVersionUpdate,
    db_session: DatabaseSessionDependency,
) -> PromptTemplateRead:
    """Switch the single active Prompt version."""

    return await set_active_prompt_version(db_session, template_name, payload)


@router.patch(
    "/internal/prompt-templates/{template_name}/status",
    response_model=PromptTemplateRead,
    dependencies=internal_key,
)
async def patch_internal_prompt_template_status(
    template_name: str,
    payload: PromptTemplateStatusUpdate,
    db_session: DatabaseSessionDependency,
) -> PromptTemplateRead:
    """Enable or disable one Prompt template with an auditable actor record."""

    return await update_prompt_template_status(db_session, template_name, payload)


@router.post(
    "/internal/model-catalog-versions",
    response_model=ModelCapabilityRead,
    dependencies=internal_key,
)
async def post_internal_model_catalog_version(
    payload: ModelCatalogVersionCreate,
    db_session: DatabaseSessionDependency,
) -> ModelCapabilityRead:
    """Persist one immutable model capability snapshot."""

    return await create_model_catalog_version(db_session, payload)


@router.patch(
    "/internal/model-catalog-versions/{catalog_version_id}/status",
    response_model=ModelCapabilityRead,
    dependencies=internal_key,
)
async def patch_internal_model_catalog_status(
    catalog_version_id: str,
    payload: ModelCatalogStatusUpdate,
    db_session: DatabaseSessionDependency,
) -> ModelCapabilityRead:
    """Enable or disable one capability snapshot."""

    return await update_model_catalog_status(db_session, catalog_version_id, payload)
