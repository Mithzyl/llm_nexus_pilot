"""Provider discovery controller."""

from fastapi import APIRouter

from nexuspilot_api.core.dependencies import ProviderRegistryDependency
from nexuspilot_api.schemas.providers import ProviderCatalogResponse

router = APIRouter(tags=["providers"])


@router.get("/providers", response_model=ProviderCatalogResponse)
async def get_providers(registry: ProviderRegistryDependency) -> ProviderCatalogResponse:
    """List registered providers and models allowed by server-side configuration."""

    configured_models = registry.configured_models_by_provider
    return ProviderCatalogResponse(
        providers=list(registry.registered_names),
        models_by_provider={
            provider_name: list(model_names)
            for provider_name, model_names in configured_models.items()
        },
    )
