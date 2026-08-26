"""FastAPI dependency assembly for Agent workflow execution services."""

from typing import Annotated

from fastapi import Depends
from nexuspilot_models.pricing import PriceCatalog
from nexuspilot_models.registry import ProviderRegistry
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from nexuspilot_api.core.dependencies import (
    get_price_catalog,
    get_provider_registry,
)
from nexuspilot_api.features.agent_runtime.services.workflow_execution_service import (
    AgentWorkflowExecutionService,
)
from nexuspilot_api.infrastructure.database import (
    get_database_session,
    get_database_session_factory,
)
from nexuspilot_api.infrastructure.object_storage import ObjectStorage, get_object_storage


def get_agent_workflow_execution_service(
    registry: Annotated[ProviderRegistry, Depends(get_provider_registry)],
    prices: Annotated[PriceCatalog, Depends(get_price_catalog)],
    db_session: Annotated[AsyncSession, Depends(get_database_session)],
    db_session_factory: Annotated[
        async_sessionmaker[AsyncSession],
        Depends(get_database_session_factory),
    ],
    storage: Annotated[ObjectStorage, Depends(get_object_storage)],
) -> AgentWorkflowExecutionService:
    """Create one request-scoped sequential Agent workflow execution service."""

    return AgentWorkflowExecutionService(
        registry=registry,
        prices=prices,
        db_session=db_session,
        db_session_factory=db_session_factory,
        storage=storage,
    )


AgentWorkflowExecutionServiceDependency = Annotated[
    AgentWorkflowExecutionService,
    Depends(get_agent_workflow_execution_service),
]
