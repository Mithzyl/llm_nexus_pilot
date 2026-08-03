"""Compose resource controllers under the authenticated versioned API prefix."""

from fastapi import APIRouter, Depends

from nexuspilot_api.core.security import require_api_key
from nexuspilot_api.features.memory.routers import (
    internal_agent_working_memory,
    internal_collaboration_memory,
    memories,
    projects,
    session_memory,
    user_memory,
)
from nexuspilot_api.routers import (
    context_builds,
    evaluations,
    internal_audit,
    knowledge,
    model_attempts,
    prompt_catalog,
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
api_router.include_router(session_memory.router)
api_router.include_router(session_memory.internal_router)
api_router.include_router(memories.router)
api_router.include_router(runs.router)
api_router.include_router(tasks.router)
api_router.include_router(model_attempts.router)
api_router.include_router(run_artifacts.router)
api_router.include_router(internal_audit.router)
api_router.include_router(projects.router)
api_router.include_router(user_memory.router)
api_router.include_router(context_builds.router)
api_router.include_router(knowledge.router)
api_router.include_router(prompt_catalog.router)
api_router.include_router(evaluations.router)
api_router.include_router(internal_agent_working_memory.router)
api_router.include_router(internal_collaboration_memory.router)

__all__ = ["api_router"]
