"""FastAPI application entry point for the NexusPilot foundation service."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from nexuspilot_api.api import router
from nexuspilot_api.config import get_settings
from nexuspilot_api.database import engine


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Own process-level resources and dispose database connections on shutdown."""

    del app
    yield
    await engine.dispose()


app = FastAPI(
    title="NexusPilot LLM Platform API",
    version="0.1.0",
    description="Phase-one run, task, model-call, and artifact persistence service.",
    lifespan=lifespan,
)
app.include_router(router)


@app.get("/health", tags=["system"])
async def health() -> dict[str, str]:
    """Return a dependency-free liveness response for local and container probes."""

    return {"status": "ok", "environment": get_settings().environment}
