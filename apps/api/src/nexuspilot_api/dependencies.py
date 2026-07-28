"""FastAPI dependency aliases for application-level model services."""

from typing import Annotated

from fastapi import Depends, Request
from nexuspilot_models.pricing import PriceCatalog
from nexuspilot_models.registry import ProviderRegistry
from sqlalchemy.ext.asyncio import AsyncSession

from nexuspilot_api.database import get_session
from nexuspilot_api.model_invocation import ModelInvocationService
from nexuspilot_api.storage import ObjectStorage, get_object_storage


def get_provider_registry(request: Request) -> ProviderRegistry:
    """Return the provider registry created by the FastAPI application lifespan."""

    return request.app.state.provider_registry


def get_price_catalog(request: Request) -> PriceCatalog:
    """Return the immutable price catalog created at application startup."""

    return request.app.state.price_catalog


def get_model_invocation_service(
    registry: Annotated[ProviderRegistry, Depends(get_provider_registry)],
    prices: Annotated[PriceCatalog, Depends(get_price_catalog)],
    session: Annotated[AsyncSession, Depends(get_session)],
    storage: Annotated[ObjectStorage, Depends(get_object_storage)],
) -> ModelInvocationService:
    """Create a request-scoped invocation service around shared providers and pricing."""

    return ModelInvocationService(
        registry=registry,
        prices=prices,
        session=session,
        storage=storage,
    )


ModelInvocationServiceDependency = Annotated[
    ModelInvocationService,
    Depends(get_model_invocation_service),
]
ProviderRegistryDependency = Annotated[
    ProviderRegistry,
    Depends(get_provider_registry),
]
