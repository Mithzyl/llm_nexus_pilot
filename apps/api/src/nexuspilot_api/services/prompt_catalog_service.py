"""Prompt rendering and Model Catalog capability use cases."""

import re

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from nexuspilot_api.core.errors import (
    InvalidRequestError,
    ResourceConflictError,
    ResourceNotFoundError,
)
from nexuspilot_api.models import (
    LlmModelCatalogVersion,
    LlmPromptTemplate,
    LlmPromptTemplateChange,
    LlmPromptTemplateVersion,
    ModelCatalogStatus,
    PromptTemplateStatus,
    new_id,
)
from nexuspilot_api.models.base import utc_now
from nexuspilot_api.schemas.prompt_catalog import (
    ModelCapabilityRead,
    ModelCatalogStatusUpdate,
    ModelCatalogVersionCreate,
    PromptActiveVersionUpdate,
    PromptRenderCreate,
    PromptRenderRead,
    PromptTemplateCreate,
    PromptTemplateRead,
    PromptTemplateVersionCreate,
)

MAX_RENDERED_PROMPT_CHARACTERS = 40_000
_VARIABLE_PATTERN = re.compile(r"\{\{\s*([A-Za-z0-9_]+)\s*\}\}")


async def create_prompt_template(
    db_session: AsyncSession,
    payload: PromptTemplateCreate,
) -> PromptTemplateRead:
    """Create one Prompt template with its first immutable version."""

    template = LlmPromptTemplate(
        template_name=payload.template_name,
        purpose=payload.purpose,
        status=PromptTemplateStatus.ENABLED,
        current_version_number=1,
    )
    db_session.add(template)
    db_session.add(
        LlmPromptTemplateVersion(
            template_version_id=new_id(),
            template_name=payload.template_name,
            version_number=1,
            content_text=payload.content_text,
            variable_schema_json=payload.variable_schema,
            created_by_actor_id=payload.created_by_actor_id,
        )
    )
    db_session.add(
        LlmPromptTemplateChange(
            change_id=new_id(),
            template_name=payload.template_name,
            action="created",
            version_number=1,
            actor_id=payload.created_by_actor_id,
        )
    )
    try:
        await db_session.commit()
    except IntegrityError as exc:
        await db_session.rollback()
        raise ResourceConflictError(
            "Prompt template already exists"
        ) from exc
    return await get_prompt_template(db_session, payload.template_name)


async def get_prompt_template(
    db_session: AsyncSession,
    template_name: str,
) -> PromptTemplateRead:
    """Return one Prompt template and its active immutable version."""

    template = await db_session.get(LlmPromptTemplate, template_name)
    if template is None:
        raise ResourceNotFoundError("Prompt Template")
    version = await db_session.scalar(
        select(LlmPromptTemplateVersion).where(
            LlmPromptTemplateVersion.template_name == template_name,
            LlmPromptTemplateVersion.version_number
            == template.current_version_number,
        )
    )
    if version is None:
        raise ResourceConflictError("Prompt template active version is unavailable")
    return PromptTemplateRead(
        template_name=template.template_name,
        purpose=template.purpose,
        status=template.status,
        current_version_number=template.current_version_number,
        content_text=version.content_text,
        variable_schema=version.variable_schema_json,
        created_at=template.created_at,
        updated_at=template.updated_at,
    )


async def create_prompt_template_version(
    db_session: AsyncSession,
    template_name: str,
    payload: PromptTemplateVersionCreate,
) -> PromptTemplateRead:
    """Append one immutable version and activate it atomically."""

    template = await db_session.scalar(
        select(LlmPromptTemplate)
        .where(LlmPromptTemplate.template_name == template_name)
        .with_for_update()
    )
    if template is None:
        raise ResourceNotFoundError("Prompt Template")
    next_version = template.current_version_number + 1
    db_session.add(
        LlmPromptTemplateVersion(
            template_version_id=new_id(),
            template_name=template_name,
            version_number=next_version,
            content_text=payload.content_text,
            variable_schema_json=payload.variable_schema,
            created_by_actor_id=payload.created_by_actor_id,
        )
    )
    template.current_version_number = next_version
    db_session.add(
        LlmPromptTemplateChange(
            change_id=new_id(),
            template_name=template_name,
            action="version_added",
            version_number=next_version,
            actor_id=payload.created_by_actor_id,
        )
    )
    await db_session.commit()
    return await get_prompt_template(db_session, template_name)


async def set_active_prompt_version(
    db_session: AsyncSession,
    template_name: str,
    payload: PromptActiveVersionUpdate,
) -> PromptTemplateRead:
    """Switch the single active Prompt version under the template row lock."""

    template = await db_session.scalar(
        select(LlmPromptTemplate)
        .where(LlmPromptTemplate.template_name == template_name)
        .with_for_update()
    )
    if template is None:
        raise ResourceNotFoundError("Prompt Template")
    version = await db_session.scalar(
        select(LlmPromptTemplateVersion).where(
            LlmPromptTemplateVersion.template_name == template_name,
            LlmPromptTemplateVersion.version_number == payload.version_number,
        )
    )
    if version is None:
        raise ResourceNotFoundError("Prompt Template Version")
    template.current_version_number = payload.version_number
    db_session.add(
        LlmPromptTemplateChange(
            change_id=new_id(),
            template_name=template_name,
            action="activated",
            version_number=payload.version_number,
            actor_id=payload.actor_id,
        )
    )
    await db_session.commit()
    return await get_prompt_template(db_session, template_name)


async def render_prompt(
    db_session: AsyncSession,
    payload: PromptRenderCreate,
) -> PromptRenderRead:
    """Render one enabled Prompt version without invoking any model."""

    template = await db_session.get(LlmPromptTemplate, payload.template_name)
    if template is None:
        raise ResourceNotFoundError("Prompt Template")
    if template.status != PromptTemplateStatus.ENABLED:
        raise ResourceConflictError("Prompt template is disabled")
    version_number = payload.version_number or template.current_version_number
    version = await db_session.scalar(
        select(LlmPromptTemplateVersion).where(
            LlmPromptTemplateVersion.template_name == payload.template_name,
            LlmPromptTemplateVersion.version_number == version_number,
        )
    )
    if version is None:
        raise ResourceNotFoundError("Prompt Template Version")
    declared_variables = set(version.variable_schema_json)
    provided_variables = set(payload.variables)
    if not declared_variables.issubset(provided_variables):
        missing = sorted(declared_variables - provided_variables)
        raise InvalidRequestError(f"Missing prompt variables: {missing}")
    if not provided_variables.issubset(declared_variables):
        unknown = sorted(provided_variables - declared_variables)
        raise InvalidRequestError(f"Unknown prompt variables: {unknown}")
    rendered = _substitute_variables(version.content_text, payload.variables)
    if len(rendered) > MAX_RENDERED_PROMPT_CHARACTERS:
        raise InvalidRequestError("Rendered prompt exceeds the allowed length")
    return PromptRenderRead(
        template_name=template.template_name,
        version_number=version.version_number,
        rendered_text=rendered,
        rendered_token_estimate=max(1, len(rendered.encode())),
        variable_names=sorted(declared_variables),
    )


def _substitute_variables(content_text: str, variables: dict[str, str]) -> str:
    """Replace each declared {{ variable }} exactly once with its rendered value."""

    def _replace(match: re.Match[str]) -> str:
        """Return the caller-provided value for one validated template variable."""

        return variables[match.group(1)]

    return _VARIABLE_PATTERN.sub(_replace, content_text)


async def create_model_catalog_version(
    db_session: AsyncSession,
    payload: ModelCatalogVersionCreate,
) -> ModelCapabilityRead:
    """Persist one immutable model capability snapshot."""

    entry = LlmModelCatalogVersion(
        catalog_version_id=new_id(),
        provider=payload.provider,
        model=payload.model,
        catalog_version=payload.catalog_version,
        source=payload.source,
        confirmed_at=utc_now(),
        status=ModelCatalogStatus.ENABLED,
        context_window=payload.context_window,
        supports_tools=payload.supports_tools,
        supports_structured_output=payload.supports_structured_output,
        supports_vision=payload.supports_vision,
        supports_caching=payload.supports_caching,
        supports_streaming=payload.supports_streaming,
        tokenizer_name=payload.tokenizer_name,
        tokenizer_version=payload.tokenizer_version,
        capabilities_json=payload.capabilities_json,
    )
    db_session.add(entry)
    try:
        await db_session.commit()
    except IntegrityError as exc:
        await db_session.rollback()
        raise ResourceConflictError(
            "Model catalog version already exists for this provider/model/version"
        ) from exc
    return await get_model_capability(
        db_session, payload.provider, payload.model, payload.catalog_version
    )


async def update_model_catalog_status(
    db_session: AsyncSession,
    catalog_version_id: str,
    payload: ModelCatalogStatusUpdate,
) -> ModelCapabilityRead:
    """Enable or disable one immutable capability snapshot."""

    entry = await db_session.get(LlmModelCatalogVersion, catalog_version_id)
    if entry is None:
        raise ResourceNotFoundError("Model Catalog Version")
    entry.status = payload.status
    await db_session.commit()
    await db_session.refresh(entry)
    return await get_model_capability(
        db_session, entry.provider, entry.model, entry.catalog_version
    )


async def get_model_capability(
    db_session: AsyncSession,
    provider: str,
    model: str,
    catalog_version: str | None,
) -> ModelCapabilityRead:
    """Return one enabled capability snapshot or an explicit unknown answer."""

    statement = select(LlmModelCatalogVersion).where(
        LlmModelCatalogVersion.provider == provider,
        LlmModelCatalogVersion.model == model,
    )
    if catalog_version is not None:
        statement = statement.where(
            LlmModelCatalogVersion.catalog_version == catalog_version
        )
    statement = statement.order_by(
        LlmModelCatalogVersion.catalog_version.desc()
    ).limit(1)
    entry = await db_session.scalar(statement)
    if entry is None or entry.status != ModelCatalogStatus.ENABLED:
        return ModelCapabilityRead(
            provider=provider,
            model=model,
            catalog_version=catalog_version,
            known=False,
            context_window=None,
            supports_tools=None,
            supports_structured_output=None,
            supports_vision=None,
            supports_caching=None,
            supports_streaming=None,
            tokenizer_name=None,
            tokenizer_version=None,
        )
    return ModelCapabilityRead(
        provider=entry.provider,
        model=entry.model,
        catalog_version=entry.catalog_version,
        known=True,
        context_window=entry.context_window,
        supports_tools=entry.supports_tools,
        supports_structured_output=entry.supports_structured_output,
        supports_vision=entry.supports_vision,
        supports_caching=entry.supports_caching,
        supports_streaming=entry.supports_streaming,
        tokenizer_name=entry.tokenizer_name,
        tokenizer_version=entry.tokenizer_version,
    )
