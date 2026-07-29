"""Provider discovery controller."""

from fastapi import APIRouter

from nexuspilot_api.core.dependencies import ProviderRegistryDependency

router = APIRouter(tags=["providers"])


@router.get("/providers")
async def get_providers(registry: ProviderRegistryDependency) -> dict[str, list[str]]:
    """List provider adapters currently registered from server-side configuration."""

    return {"providers": [name.value for name in registry.registered_names]}
