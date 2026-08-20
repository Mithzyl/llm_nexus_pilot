"""FastAPI dependency aliases for application-level model services."""

from typing import Annotated

from fastapi import Depends, Request
from nexuspilot_models.pricing import PriceCatalog
from nexuspilot_models.registry import ProviderRegistry
from sqlalchemy.ext.asyncio import AsyncSession

from nexuspilot_api.core.config import Settings, get_settings
from nexuspilot_api.infrastructure.database import get_database_session
from nexuspilot_api.infrastructure.object_storage import ObjectStorage, get_object_storage
from nexuspilot_api.services.model_response_service import ModelInvocationService


def get_provider_registry(request: Request) -> ProviderRegistry:
    """Return the provider registry created by the FastAPI application lifespan."""

    return request.app.state.provider_registry


def get_price_catalog(request: Request) -> PriceCatalog:
    """Return the immutable price catalog created at application startup."""

    return request.app.state.price_catalog


def get_model_invocation_service(
    registry: Annotated[ProviderRegistry, Depends(get_provider_registry)],
    prices: Annotated[PriceCatalog, Depends(get_price_catalog)],
    db_session: Annotated[AsyncSession, Depends(get_database_session)],
    storage: Annotated[ObjectStorage, Depends(get_object_storage)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> ModelInvocationService:
    """Create a request-scoped invocation service around shared providers and pricing."""

    return ModelInvocationService(
        registry=registry,
        prices=prices,
        db_session=db_session,
        storage=storage,
        settings=settings,
    )


ModelInvocationServiceDependency = Annotated[
    ModelInvocationService,
    Depends(get_model_invocation_service),
]
ProviderRegistryDependency = Annotated[
    ProviderRegistry,
    Depends(get_provider_registry),
]
