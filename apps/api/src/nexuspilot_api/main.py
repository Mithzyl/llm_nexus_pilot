"""FastAPI application entry point and top-level composition boundary."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from nexuspilot_models.errors import ModelProviderError

from nexuspilot_api.core.config import get_settings
from nexuspilot_api.core.errors import (
    ApplicationError,
    InvalidCursorError,
    ResourceConflictError,
    ResourceNotFoundError,
)
from nexuspilot_api.infrastructure.database import engine
from nexuspilot_api.infrastructure.provider_registry import (
    create_price_catalog,
    create_provider_registry,
)
from nexuspilot_api.routers import api_router


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Create shared provider resources and dispose them with database connections."""

    settings = get_settings()
    http_client = httpx.AsyncClient(
        limits=httpx.Limits(max_connections=100, max_keepalive_connections=20)
    )
    app.state.provider_registry = create_provider_registry(settings, http_client)
    app.state.price_catalog = create_price_catalog(settings)
    try:
        yield
    finally:
        await http_client.aclose()
        await engine.dispose()


app = FastAPI(
    title="NexusPilot LLM Platform API",
    version="0.4.0",
    description="Provider-neutral Responses API and durable NexusPilot execution records.",
    lifespan=lifespan,
)
app.include_router(api_router)


@app.exception_handler(ApplicationError)
async def application_error_handler(_request: object, error: ApplicationError) -> JSONResponse:
    """Convert expected application failures to stable public HTTP responses."""

    status_code = {
        ResourceNotFoundError: 404,
        ResourceConflictError: 409,
        InvalidCursorError: 422,
    }.get(type(error), 400)
    return JSONResponse(status_code=status_code, content={"detail": str(error)})


@app.exception_handler(ModelProviderError)
async def model_provider_error_handler(_request: object, error: ModelProviderError) -> JSONResponse:
    """Convert provider failures to a stable public error envelope and HTTP status."""

    status_code = {
        "authentication_error": 502,
        "invalid_request": 422,
        "unsupported_capability": 422,
        "provider_not_configured": 503,
        "model_not_allowed": 422,
        "rate_limited": 503,
        "timeout": 504,
    }.get(error.error_type, 502)
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "type": error.error_type,
                "message": error.message,
                "retryable": error.retryable,
            }
        },
    )


@app.get("/health", tags=["system"])
async def health() -> dict[str, str]:
    """Return a dependency-free liveness response for local and container probes."""

    return {"status": "ok", "environment": get_settings().environment}
