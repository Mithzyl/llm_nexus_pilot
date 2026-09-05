"""FastAPI application entry point and top-level composition boundary."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from nexuspilot_models.errors import ModelProviderError

from nexuspilot_api.core.config import get_settings
from nexuspilot_api.core.errors import (
    ApplicationError,
    AuthenticationCsrfError,
    AuthenticationIdentityConflictError,
    AuthenticationRateLimitError,
    AuthenticationRequiredError,
    InvalidAuthenticationCredentialsError,
    InvalidCursorError,
    InvalidRequestError,
    ResourceConflictError,
    ResourceNotFoundError,
)
from nexuspilot_api.features.identity.routers.authentication import router as authentication_router
from nexuspilot_api.infrastructure.database import database_session_factory, engine
from nexuspilot_api.infrastructure.provider_registry import (
    create_price_catalog,
    create_provider_registry,
)
from nexuspilot_api.routers import api_router
from nexuspilot_api.services.provider_endpoint_service import (
    load_enabled_provider_base_urls,
)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Create shared provider resources and dispose them with database connections."""

    settings = get_settings()
    http_client = httpx.AsyncClient(
        limits=httpx.Limits(max_connections=100, max_keepalive_connections=20)
    )
    try:
        async with database_session_factory() as database_session:
            enabled_base_urls_by_provider = await load_enabled_provider_base_urls(database_session)
        app.state.provider_registry = create_provider_registry(
            settings,
            http_client,
            enabled_base_urls_by_provider,
        )
        app.state.price_catalog = create_price_catalog(settings)
        yield
    finally:
        await http_client.aclose()
        await engine.dispose()


app = FastAPI(
    title="NexusPilot LLM Platform API",
    version="0.8.0",
    description="Provider-neutral Responses API and durable NexusPilot execution records.",
    lifespan=lifespan,
)
app.include_router(authentication_router)
app.include_router(api_router)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, error: RequestValidationError):
    """Redact authentication validation inputs while preserving other API error contracts."""

    if request.url.path.startswith("/api/v1/auth/"):
        return JSONResponse(
            status_code=422,
            content={"detail": "认证请求格式无效，请检查填写内容。"},
            headers={"Cache-Control": "no-store"},
        )
    return await request_validation_exception_handler(request, error)


@app.exception_handler(ApplicationError)
async def application_error_handler(_request: object, error: ApplicationError) -> JSONResponse:
    """Convert expected application failures to stable public HTTP responses."""

    status_code = {
        ResourceNotFoundError: 404,
        ResourceConflictError: 409,
        InvalidCursorError: 422,
        InvalidRequestError: 422,
        AuthenticationRequiredError: 401,
        InvalidAuthenticationCredentialsError: 401,
        AuthenticationIdentityConflictError: 400,
        AuthenticationCsrfError: 403,
        AuthenticationRateLimitError: 429,
    }.get(type(error), 400)
    return JSONResponse(
        status_code=status_code,
        content={"detail": str(error)},
        headers={"Cache-Control": "no-store"},
    )


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
