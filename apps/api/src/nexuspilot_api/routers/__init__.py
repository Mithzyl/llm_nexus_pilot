"""Compose resource controllers under the authenticated versioned API prefix."""

from fastapi import APIRouter, Depends

from nexuspilot_api.core.security import require_api_key
from nexuspilot_api.routers import (
    internal_audit,
    model_attempts,
    providers,
    responses,
    run_artifacts,
    runs,
    sessions,
    tasks,
    users,
)

api_router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_api_key)])
api_router.include_router(providers.router)
api_router.include_router(responses.router)
api_router.include_router(users.router)
api_router.include_router(sessions.router)
api_router.include_router(runs.router)
api_router.include_router(tasks.router)
api_router.include_router(model_attempts.router)
api_router.include_router(run_artifacts.router)
api_router.include_router(internal_audit.router)

__all__ = ["api_router"]
