"""Compose resource controllers under the authenticated versioned API prefix."""

from fastapi import APIRouter, Depends

from nexuspilot_api.core.security import require_api_key
from nexuspilot_api.features.agent_runtime.routers import agent_workflows
from nexuspilot_api.features.identity.resource_access import authorize_resource_request
from nexuspilot_api.features.identity.routers.sso import browser_router as sso_browser_router
from nexuspilot_api.features.identity.routers.mcp_management import router as mcp_management_router
from nexuspilot_api.features.identity.routers.account_linking import browser_router as account_link_router
from nexuspilot_api.features.identity.routers.user_instructions import (
    router as user_instructions_router,
)
from nexuspilot_api.features.memory.routers import (
    internal_agent_working_memory,
    internal_collaboration_memory,
    memories,
    product_memory,
    projects,
    session_memory,
    user_memory,
)
from nexuspilot_api.features.memory.routers.sharing import router as memory_sharing_router
from nexuspilot_api.routers import (
    context_builds,
    evaluations,
    internal_audit,
    knowledge,
    model_attempts,
    project_files,
    prompt_catalog,
    providers,
    responses,
    run_artifacts,
    runs,
    sessions,
    tasks,
    user_files,
    user_interactions,
    users,
)
from nexuspilot_api.routers.prompt_scenes import router as prompt_scenes_router

api_router = APIRouter(
    prefix="/api/v1",
    dependencies=[Depends(require_api_key), Depends(authorize_resource_request)],
)
api_router.include_router(providers.router)
api_router.include_router(responses.router)
api_router.include_router(user_interactions.router)
api_router.include_router(users.router)
api_router.include_router(user_files.router)
api_router.include_router(sessions.router)
api_router.include_router(session_memory.router)
api_router.include_router(session_memory.internal_router)
api_router.include_router(memories.router)
api_router.include_router(product_memory.router)
api_router.include_router(product_memory.management_router)
api_router.include_router(sso_browser_router)
api_router.include_router(mcp_management_router)
api_router.include_router(account_link_router)
api_router.include_router(prompt_scenes_router)
api_router.include_router(user_instructions_router)
api_router.include_router(memory_sharing_router)
api_router.include_router(runs.router)
api_router.include_router(tasks.router)
api_router.include_router(model_attempts.router)
api_router.include_router(run_artifacts.router)
api_router.include_router(internal_audit.router)
api_router.include_router(projects.router)
api_router.include_router(project_files.router)
api_router.include_router(user_memory.router)
api_router.include_router(context_builds.router)
api_router.include_router(knowledge.router)
api_router.include_router(prompt_catalog.router)
api_router.include_router(evaluations.router)
api_router.include_router(internal_agent_working_memory.router)
api_router.include_router(internal_collaboration_memory.router)
api_router.include_router(agent_workflows.router)

__all__ = ["api_router"]
