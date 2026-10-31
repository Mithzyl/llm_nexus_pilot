"""Service-authenticated App Runs using existing workflow routes and execution services."""

import secrets
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from nexuspilot_api.features.agent_runtime.dependencies import (
    AgentWorkflowExecutionServiceDependency,
)
from nexuspilot_api.features.agent_runtime.routers.agent_workflows import router as workflows
from nexuspilot_api.features.identity.models.applications import (
    Application,
    ApplicationUserMapping,
    RunApplicationContext,
)
from nexuspilot_api.features.identity.resource_access import (
    OWNER_SESSION_KEY,
    validate_resource_references,
)
from nexuspilot_api.features.identity.routers.user_instructions import (
    router as user_instructions_router,
)
from nexuspilot_api.features.identity.services.application_identity import resolve_application_user
from nexuspilot_api.features.memory.routers.product_memory import app_management_router
from nexuspilot_api.features.memory.routers.product_memory import router as memory_router
from nexuspilot_api.features.memory.routers.sharing import router as memory_sharing_router
from nexuspilot_api.models import LlmAgentWorkflowExecution
from nexuspilot_api.routers.common import DatabaseSessionDependency
from nexuspilot_api.routers.user_interactions import router as interaction_router
from nexuspilot_api.schemas.runs import RunCreate, RunRead
from nexuspilot_api.services.application_tools import (
    STATIC_APP_TOOLS,
    application_tool_configurations,
)
from nexuspilot_api.services.run_service import cancel_run, create_run


async def authenticate_application(request: Request, db: DatabaseSessionDependency):
    """Pin both owner and App mapping before authorizing any persisted Run references."""
    if request.cookies.get("nexuspilot_session"):
        raise HTTPException(400, "Browser and application identities cannot be combined")
    app_id = request.headers.get("x-application-id", "")
    config = application_tool_configurations().get(app_id)
    token = request.headers.get("authorization", "")
    if config is None or not secrets.compare_digest(
        token.encode(), ("Bearer " + config.inbound_key.get_secret_value()).encode()
    ):
        raise HTTPException(401, "Invalid application credential")
    subject = request.headers.get("x-external-user-id", "")
    if not 1 <= len(subject) <= 128:
        raise HTTPException(422, "Application subject is required")
    # Only a configured, authenticated backend can provision its first App row.
    app = await db.get(Application, app_id)
    if app is None:
        from sqlalchemy.exc import IntegrityError

        try:
            async with db.begin_nested():
                db.add(Application(application_id=app_id))
                await db.flush()
        except IntegrityError:
            if (
                await db.scalar(
                    select(Application)
                    .where(Application.application_id == app_id)
                    .with_for_update()
                )
                is None
            ):
                raise
        await db.commit()
    mapping = await resolve_application_user(db, application_id=app_id, external_user_id=subject)
    db.info[OWNER_SESSION_KEY] = mapping.pilot_principal_id
    db.info["authorized_application_mapping_id"] = mapping.mapping_id
    db.info["memory_tools_allowed"] = True
    db.info["memory_api_entry"] = mapping.application_id
    await validate_resource_references(db, request.path_params, mapping.pilot_principal_id)
    for key, value in request.query_params.multi_items():
        await validate_resource_references(db, {key: value}, mapping.pilot_principal_id)
    if request.method == "POST" and request.url.path.endswith("/agent-workflows"):
        body = await request.json()
        if (
            not isinstance(body, dict)
            or body.get("workflow_name") != "general_agent"
            or body.get("workflow_version") != "1.0.0"
            or body.get("execution_profile") != "general_tools_v1"
        ):
            raise HTTPException(422, "Application Runs require the general Agent definition")
        await validate_resource_references(db, body, mapping.pilot_principal_id)
        context = await db.get(RunApplicationContext, request.path_params["run_id"])
        if context is None or context.mapping_id != mapping.mapping_id:
            raise HTTPException(404, "Application Run not found")
    return mapping


ApplicationIdentity = Annotated[ApplicationUserMapping, Depends(authenticate_application)]
router = APIRouter(
    prefix="/api/v1/app",
    tags=["application-runs"],
    dependencies=[Depends(authenticate_application)],
)


class ApplicationRunCreate(BaseModel):
    """Accept an objective only; backend authentication supplies ownership and tools."""

    model_config = ConfigDict(extra="forbid")
    user_request: str = Field(min_length=1, max_length=100_000)
    scene_id: str | None = Field(default=None, min_length=1, max_length=64)


@router.get("/runs/{run_id}/diagnostics")
async def get_application_diagnostics(
    run_id: str,
    identity: ApplicationIdentity,
    db: DatabaseSessionDependency,
    response: Response,
    limit: int = Query(50, ge=1, le=100),
    after_attempt_id: str | None = Query(None, min_length=36, max_length=36),
    after_tool_call_id: str | None = Query(None, min_length=36, max_length=36),
):
    """Return no-store structural diagnostics for the exact authenticated App Run."""
    from nexuspilot_api.features.identity.services.application_diagnostics import (
        read_application_diagnostics,
    )

    response.headers["Cache-Control"] = "no-store"
    return await read_application_diagnostics(
        db,
        identity,
        run_id,
        limit=limit,
        after_attempt_id=after_attempt_id,
        after_tool_call_id=after_tool_call_id,
    )


@router.post("/runs", response_model=RunRead, status_code=201)
async def post_application_run(
    payload: ApplicationRunCreate, identity: ApplicationIdentity, db: DatabaseSessionDependency
):
    """Create a Run and immutable tool grant together, using the existing body storage service."""
    config = application_tool_configurations()[identity.application_id]
    run = await create_run(
        db,
        RunCreate(
            user_request=payload.user_request,
            user_id=identity.pilot_principal_id,
            scene_id=payload.scene_id,
        ),
        commit=False,
    )
    db.add(
        RunApplicationContext(
            run_id=run.run_id,
            mapping_id=identity.mapping_id,
            agent_id="general_agent",
            allowed_tool_versions={
                t.name: t.version
                for t in STATIC_APP_TOOLS[identity.application_id]
                if t.name in config.operations
            },
        )
    )
    await db.commit()
    return RunRead.model_validate(run)


@router.post("/runs/{run_id}/cancel", response_model=RunRead)
async def cancel_application_run(
    run_id: str,
    db: DatabaseSessionDependency,
    service: AgentWorkflowExecutionServiceDependency,
):
    """Cancel the Run and its workflow so both App views persist the same terminal state.

    Cancel the Run first to prevent a concurrent start from creating new work.
    The shared workflow service finalizes nodes and emits its durable cancellation
    event; late model usage remains handled by the existing execution reconciler.
    """
    run = await cancel_run(db, run_id)
    workflow_id = await db.scalar(
        select(LlmAgentWorkflowExecution.workflow_execution_id).where(
            LlmAgentWorkflowExecution.run_id == run_id
        )
    )
    if workflow_id is not None:
        await service.cancel_workflow_execution(workflow_id)
        await db.refresh(run)
    return RunRead.model_validate(run)


# Reuse JSON, SSE, replay and result handlers instead of creating an App-specific loop.
router.include_router(workflows)

router.include_router(memory_router)
router.include_router(app_management_router)
router.include_router(user_instructions_router)
router.include_router(memory_sharing_router)

router.include_router(interaction_router)
