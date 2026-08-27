"""Prompt rendering and Model Catalog capability use cases."""

from re import Match

from nexuspilot_models.contracts import ModelReasoningCapabilities
from nexuspilot_models.registry import ProviderRegistry
from sqlalchemy import func, select
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
    LlmRegisteredModel,
    ModelCatalogStatus,
    ModelRegistrationStatus,
    PromptTemplateStatus,
    new_id,
)
from nexuspilot_api.models.base import utc_now
from nexuspilot_api.schemas.prompt_catalog import (
    PROMPT_VARIABLE_PATTERN,
    ModelCapabilityRead,
    ModelCatalogStatusUpdate,
    ModelCatalogVersionCreate,
    ModelDirectoryEntryRead,
    ModelUnavailableReason,
    PromptActiveVersionUpdate,
    PromptRenderCreate,
    PromptRenderRead,
    PromptTemplateCreate,
    PromptTemplateOptionRead,
    PromptTemplateRead,
    PromptTemplateStatusUpdate,
    PromptTemplateVersionCreate,
    PromptVariableValue,
    RegisteredModelBootstrapRead,
    RegisteredModelCreate,
    RegisteredModelRead,
    RegisteredModelUpdate,
)

MAX_RENDERED_PROMPT_CHARACTERS = 40_000


async def list_enabled_prompt_template_options(
    db_session: AsyncSession,
) -> list[PromptTemplateOptionRead]:
    """List enabled active Prompt versions without exposing immutable source text."""

    rows = (
        await db_session.execute(
            select(LlmPromptTemplate, LlmPromptTemplateVersion)
            .join(
                LlmPromptTemplateVersion,
                (LlmPromptTemplateVersion.template_name == LlmPromptTemplate.template_name)
                & (
                    LlmPromptTemplateVersion.version_number
                    == LlmPromptTemplate.current_version_number
                ),
            )
            .where(LlmPromptTemplate.status == PromptTemplateStatus.ENABLED)
            .order_by(LlmPromptTemplate.template_name)
        )
    ).all()
    return [
        PromptTemplateOptionRead(
            template_name=template.template_name,
            purpose=template.purpose,
            current_version_number=template.current_version_number,
            variable_schema=version.variable_schema_json,
        )
        for template, version in rows
    ]


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

    template = await db_session.scalar(
        select(LlmPromptTemplate)
        .where(LlmPromptTemplate.template_name == template_name)
        .execution_options(populate_existing=True)
    )
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
    latest_version_number = await db_session.scalar(
        select(func.max(LlmPromptTemplateVersion.version_number)).where(
            LlmPromptTemplateVersion.template_name == template_name
        )
    )
    next_version = (latest_version_number or 0) + 1
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
    try:
        await db_session.commit()
    except IntegrityError as exc:
        await db_session.rollback()
        raise ResourceConflictError(
            "Prompt template version was created concurrently"
        ) from exc
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
    _validate_prompt_variable_values(version.variable_schema_json, payload.variables)
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


def _validate_prompt_variable_values(
    variable_schema: dict[str, str],
    variables: dict[str, PromptVariableValue],
) -> None:
    """Enforce each rendered value against its immutable declared scalar type."""

    expected_python_types = {
        "string": str,
        "integer": int,
        "number": (int, float),
        "boolean": bool,
    }
    for variable_name, declared_type in variable_schema.items():
        expected_type = expected_python_types.get(declared_type)
        if expected_type is None:
            raise ResourceConflictError(
                f"Prompt variable {variable_name} has an unsupported stored type"
            )
        value = variables[variable_name]
        if declared_type in {"integer", "number"} and isinstance(value, bool):
            raise InvalidRequestError(
                f"Prompt variable {variable_name} must be {declared_type}"
            )
        if not isinstance(value, expected_type):
            raise InvalidRequestError(
                f"Prompt variable {variable_name} must be {declared_type}"
            )


def _render_prompt_variable(value: PromptVariableValue) -> str:
    """Render one validated scalar with deterministic boolean formatting."""

    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _substitute_variables(
    content_text: str,
    variables: dict[str, PromptVariableValue],
) -> str:
    """Replace each declared {{ variable }} exactly once with its rendered value."""

    def _replace(match: Match[str]) -> str:
        """Return the caller-provided value for one validated template variable."""

        return _render_prompt_variable(variables[match.group(1)])

    return PROMPT_VARIABLE_PATTERN.sub(_replace, content_text)


async def update_prompt_template_status(
    db_session: AsyncSession,
    template_name: str,
    payload: PromptTemplateStatusUpdate,
) -> PromptTemplateRead:
    """Enable or disable one Prompt template and record the actor transition."""

    template = await db_session.scalar(
        select(LlmPromptTemplate)
        .where(LlmPromptTemplate.template_name == template_name)
        .with_for_update()
    )
    if template is None:
        raise ResourceNotFoundError("Prompt Template")
    template.status = payload.status
    db_session.add(
        LlmPromptTemplateChange(
            change_id=new_id(),
            template_name=template_name,
            action=payload.status.value.lower(),
            version_number=template.current_version_number,
            actor_id=payload.actor_id,
        )
    )
    await db_session.commit()
    return await get_prompt_template(db_session, template_name)


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


async def create_registered_model(
    db_session: AsyncSession,
    payload: RegisteredModelCreate,
) -> RegisteredModelRead:
    """Persist one stable model identity; duplicate provider/model pairs fail explicitly."""

    registered_model = LlmRegisteredModel(
        model_id=new_id(),
        provider=payload.provider,
        model_identifier=payload.model_identifier,
        display_name=payload.display_name,
        description=payload.description,
        status=payload.status,
        sort_order=payload.sort_order,
        metadata_json=payload.metadata_json,
    )
    db_session.add(registered_model)
    try:
        await db_session.commit()
    except IntegrityError as exc:
        await db_session.rollback()
        raise ResourceConflictError(
            "Registered model already exists for this provider/model identifier"
        ) from exc
    await db_session.refresh(registered_model)
    return _registered_model_read(registered_model)


async def update_registered_model(
    db_session: AsyncSession,
    model_id: str,
    payload: RegisteredModelUpdate,
) -> RegisteredModelRead:
    """Update mutable display or availability fields without changing stable identity."""

    registered_model = await db_session.get(LlmRegisteredModel, model_id)
    if registered_model is None:
        raise ResourceNotFoundError("Registered Model")
    for field_name, field_value in payload.model_dump(exclude_unset=True).items():
        setattr(registered_model, field_name, field_value)
    await db_session.commit()
    await db_session.refresh(registered_model)
    return _registered_model_read(registered_model)


async def bootstrap_registered_models(
    db_session: AsyncSession,
    registry: ProviderRegistry,
) -> RegisteredModelBootstrapRead:
    """Explicitly persist configured allowlist models and remain idempotent on repeats."""

    configured_pairs = sorted(
        (provider, model_identifier)
        for provider, model_identifiers in registry.configured_models_by_provider.items()
        for model_identifier in model_identifiers
    )
    if not configured_pairs:
        return RegisteredModelBootstrapRead(created_count=0, existing_count=0)
    existing_pairs = {
        (provider, model_identifier)
        for provider, model_identifier in (
            await db_session.execute(
                select(
                    LlmRegisteredModel.provider,
                    LlmRegisteredModel.model_identifier,
                ).where(
                    LlmRegisteredModel.provider.in_(
                        {provider for provider, _model in configured_pairs}
                    )
                )
            )
        ).all()
    }
    missing_pairs = [pair for pair in configured_pairs if pair not in existing_pairs]
    sort_order_by_pair = {
        pair: sort_order
        for sort_order, pair in enumerate(configured_pairs, start=1)
    }
    for provider, model_identifier in missing_pairs:
        db_session.add(
            LlmRegisteredModel(
                model_id=new_id(),
                provider=provider,
                model_identifier=model_identifier,
                display_name=model_identifier,
                description=None,
                status=ModelRegistrationStatus.ENABLED,
                sort_order=sort_order_by_pair[(provider, model_identifier)],
                metadata_json={"registration_source": "provider_registry_bootstrap"},
            )
        )
    try:
        await db_session.commit()
    except IntegrityError as exc:
        # A concurrent bootstrap may win the unique-key race; only accept it when
        # every requested pair is now present rather than masking another failure.
        await db_session.rollback()
        persisted_pairs = {
            (provider, model_identifier)
            for provider, model_identifier in (
                await db_session.execute(
                    select(
                        LlmRegisteredModel.provider,
                        LlmRegisteredModel.model_identifier,
                    )
                )
            ).all()
        }
        if all(pair in persisted_pairs for pair in configured_pairs):
            return RegisteredModelBootstrapRead(
                created_count=0,
                existing_count=len(configured_pairs),
            )
        raise ResourceConflictError("Registered model bootstrap conflicted") from exc
    return RegisteredModelBootstrapRead(
        created_count=len(missing_pairs),
        existing_count=len(configured_pairs) - len(missing_pairs),
    )


async def list_model_directory(
    db_session: AsyncSession,
    registry: ProviderRegistry,
    *,
    requires_streaming: bool,
    requires_structured_output: bool,
    requires_tools: bool,
    requires_vision: bool,
) -> list[ModelDirectoryEntryRead]:
    """Join stable registrations, Registry allowlists, and newest enabled capabilities."""

    registered_models = (
        await db_session.scalars(
            select(LlmRegisteredModel).order_by(
                LlmRegisteredModel.provider,
                LlmRegisteredModel.sort_order,
                LlmRegisteredModel.display_name,
                LlmRegisteredModel.model_identifier,
                LlmRegisteredModel.model_id,
            )
        )
    ).all()
    catalog_versions = (
        await db_session.scalars(
            select(LlmModelCatalogVersion)
            .where(LlmModelCatalogVersion.status == ModelCatalogStatus.ENABLED)
            .order_by(
                LlmModelCatalogVersion.provider,
                LlmModelCatalogVersion.model,
                LlmModelCatalogVersion.confirmed_at.desc(),
                LlmModelCatalogVersion.created_at.desc(),
                LlmModelCatalogVersion.catalog_version_id.desc(),
            )
        )
    ).all()
    latest_capability_by_provider_model: dict[
        tuple[str, str], LlmModelCatalogVersion
    ] = {}
    for catalog_version in catalog_versions:
        latest_capability_by_provider_model.setdefault(
            (catalog_version.provider, catalog_version.model), catalog_version
        )

    registered_provider_names = set(registry.registered_names)
    configured_models_by_provider = registry.configured_models_by_provider
    directory_entries: list[ModelDirectoryEntryRead] = []
    for registered_model in registered_models:
        provider = registered_model.provider
        configured_models = configured_models_by_provider.get(provider)
        is_provider_registered = provider in registered_provider_names
        is_model_allowed = bool(is_provider_registered) and (
            not configured_models
            or registered_model.model_identifier in configured_models
        )
        capability = latest_capability_by_provider_model.get(
            (provider.value, registered_model.model_identifier)
        )
        unavailable_reason = _model_unavailable_reason(
            registered_model=registered_model,
            is_provider_registered=is_provider_registered,
            is_model_allowed=is_model_allowed,
            capability=capability,
            requires_streaming=requires_streaming,
            requires_structured_output=requires_structured_output,
            requires_tools=requires_tools,
            requires_vision=requires_vision,
        )
        reasoning_capabilities = (
            registry.reasoning_capabilities(provider, registered_model.model_identifier)
            if is_model_allowed
            else ModelReasoningCapabilities()
        )
        directory_entries.append(
            _model_directory_entry_read(
                registered_model,
                capability,
                registry_available=is_model_allowed,
                unavailable_reason=unavailable_reason,
                reasoning_capabilities=reasoning_capabilities,
            )
        )
    return directory_entries


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

    entry = await resolve_enabled_model_catalog_version(
        db_session, provider, model, catalog_version
    )
    if entry is None:
        return ModelCapabilityRead(
            provider=provider,
            model=model,
            catalog_version_id=None,
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
    return _model_capability_read(entry)


async def resolve_enabled_model_catalog_version(
    db_session: AsyncSession,
    provider: str,
    model: str,
    catalog_version: str | None = None,
) -> LlmModelCatalogVersion | None:
    """Resolve the newest enabled immutable capability snapshot by creation evidence."""

    statement = select(LlmModelCatalogVersion).where(
        LlmModelCatalogVersion.provider == provider,
        LlmModelCatalogVersion.model == model,
        LlmModelCatalogVersion.status == ModelCatalogStatus.ENABLED,
    )
    if catalog_version is not None:
        statement = statement.where(
            LlmModelCatalogVersion.catalog_version == catalog_version
        )
    statement = statement.order_by(
        LlmModelCatalogVersion.confirmed_at.desc(),
        LlmModelCatalogVersion.created_at.desc(),
        LlmModelCatalogVersion.catalog_version_id.desc(),
    ).limit(1)
    return await db_session.scalar(statement)


def _model_capability_read(entry: LlmModelCatalogVersion) -> ModelCapabilityRead:
    """Map one enabled catalog row to the public capability contract."""

    return ModelCapabilityRead(
        provider=entry.provider,
        model=entry.model,
        catalog_version_id=entry.catalog_version_id,
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


def _registered_model_read(registered_model: LlmRegisteredModel) -> RegisteredModelRead:
    """Map one stable model row to the internal management response contract."""

    return RegisteredModelRead(
        model_id=registered_model.model_id,
        provider=registered_model.provider,
        model_identifier=registered_model.model_identifier,
        display_name=registered_model.display_name,
        description=registered_model.description,
        status=registered_model.status,
        sort_order=registered_model.sort_order,
        metadata_json=registered_model.metadata_json,
        created_at=registered_model.created_at,
        updated_at=registered_model.updated_at,
    )


def _model_unavailable_reason(
    *,
    registered_model: LlmRegisteredModel,
    is_provider_registered: bool,
    is_model_allowed: bool,
    capability: LlmModelCatalogVersion | None,
    requires_streaming: bool,
    requires_structured_output: bool,
    requires_tools: bool,
    requires_vision: bool,
) -> ModelUnavailableReason | None:
    """Return the first actionable reason a registered model cannot satisfy a request."""

    if registered_model.status != ModelRegistrationStatus.ENABLED:
        return "model_disabled"
    if not is_provider_registered:
        return "provider_unavailable"
    if not is_model_allowed:
        return "model_not_allowed"
    if capability is None:
        return "capability_unknown"
    if requires_streaming and not capability.supports_streaming:
        return "streaming_unsupported"
    if requires_structured_output and not capability.supports_structured_output:
        return "structured_output_unsupported"
    if requires_tools and not capability.supports_tools:
        return "tools_unsupported"
    if requires_vision and not capability.supports_vision:
        return "vision_unsupported"
    return None


def _model_directory_entry_read(
    registered_model: LlmRegisteredModel,
    capability: LlmModelCatalogVersion | None,
    *,
    registry_available: bool,
    unavailable_reason: ModelUnavailableReason | None,
    reasoning_capabilities: ModelReasoningCapabilities,
) -> ModelDirectoryEntryRead:
    """Compose one content-safe directory entry from persisted and runtime evidence."""

    return ModelDirectoryEntryRead(
        model_id=registered_model.model_id,
        provider=registered_model.provider,
        model_identifier=registered_model.model_identifier,
        display_name=registered_model.display_name,
        description=registered_model.description,
        status=registered_model.status,
        sort_order=registered_model.sort_order,
        metadata_json=registered_model.metadata_json,
        registry_available=registry_available,
        selectable=unavailable_reason is None,
        unavailable_reason=unavailable_reason,
        catalog_version_id=(capability.catalog_version_id if capability else None),
        catalog_version=(capability.catalog_version if capability else None),
        context_window=(capability.context_window if capability else None),
        supports_tools=(capability.supports_tools if capability else None),
        supports_structured_output=(
            capability.supports_structured_output if capability else None
        ),
        supports_vision=(capability.supports_vision if capability else None),
        supports_caching=(capability.supports_caching if capability else None),
        supports_streaming=(capability.supports_streaming if capability else None),
        tokenizer_name=(capability.tokenizer_name if capability else None),
        tokenizer_version=(capability.tokenizer_version if capability else None),
        reasoning_capabilities=reasoning_capabilities,
    )
