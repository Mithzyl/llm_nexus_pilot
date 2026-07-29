"""Compose resource controllers under the authenticated versioned API prefix."""

from fastapi import APIRouter, Depends

from nexuspilot_api.core.security import require_api_key
from nexuspilot_api.routers import artifacts, attempts, providers, responses, runs, tasks, users

api_router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_api_key)])
api_router.include_router(providers.router)
api_router.include_router(responses.router)
api_router.include_router(users.router)
api_router.include_router(runs.router)
api_router.include_router(tasks.router)
api_router.include_router(attempts.router)
api_router.include_router(artifacts.router)

__all__ = ["api_router"]
