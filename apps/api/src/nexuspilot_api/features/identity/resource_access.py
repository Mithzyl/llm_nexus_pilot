"""Authorize final-user HTTP resources before model, storage or database side effects.

Route policy is explicit. Ownership follows persisted parent references, never a
caller-declared user. Request sessions carry an owner for SQL list filtering; no
global mutable identity or process-wide ORM interception is used.
"""

from email.message import Message
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from fastapi.params import Form
from sqlalchemy import inspect, select
from sqlalchemy.ext.asyncio import AsyncSession

from nexuspilot_api import models as m
from nexuspilot_api.core.config import get_settings
from nexuspilot_api.core.errors import ResourceNotFoundError
from nexuspilot_api.core.security import require_internal_api_key
from nexuspilot_api.features.identity.dependencies import get_workspace_principal
from nexuspilot_api.features.identity.resource_policy import (
    INTERNAL_ROUTES,
    OWNED_ROUTES,
    SHARED_ROUTES,
)
from nexuspilot_api.features.identity.services.authentication_service import (
    require_owned_login_session,
    validate_authentication_origin,
    validate_login_session_csrf,
)
from nexuspilot_api.infrastructure.database import get_database_session

OWNER_SESSION_KEY = "authorized_user_id"


def uses_json_body(content_type: str | None) -> bool:
    """Match FastAPI's JSON media-type interpretation, including absent headers and +json."""
    if not content_type:
        return True
    media = Message()
    media["content-type"] = content_type
    return media.get_content_maintype() == "application" and (
        media.get_content_subtype() == "json" or media.get_content_subtype().endswith("+json")
    )


RESOURCES = {
    "user_id": m.User,
    "owner_user_id": m.User,
    "session_id": m.LlmSession,
    "message_id": m.LlmMessage,
    "current_user_message_id": m.LlmMessage,
    "parent_message_id": m.LlmMessage,
    "summary_through_message_id": m.LlmMessage,
    "tool_call_id": m.LlmModelToolCall,
    "run_id": m.LlmRun,
    "task_id": m.LlmTask,
    "parent_task_id": m.LlmTask,
    "attempt_id": m.LlmModelAttempt,
    "source_model_attempt_id": m.LlmModelAttempt,
    "continuation_from_attempt_id": m.LlmModelAttempt,
    "created_by_attempt_id": m.LlmModelAttempt,
    "candidate_attempt_id": m.LlmModelAttempt,
    "evaluator_attempt_id": m.LlmModelAttempt,
    "retry_id": m.LlmModelTransportAttempt,
    "artifact_id": m.LlmRunArtifact,
    "file_id": m.UserFile,
    "context_build_id": m.LlmContextBuild,
    "workflow_execution_id": m.LlmAgentWorkflowExecution,
    "node_execution_id": m.LlmAgentWorkflowNodeExecution,
    "agent_run_id": m.LlmAgentRun,
    "agent_handoff_id": m.LlmAgentHandoff,
    "evaluation_id": m.LlmTaskEvaluation,
    "memory_id": m.LlmMemory,
    "supersedes_memory_id": m.LlmMemory,
    "memory_retrieval_id": m.LlmMemoryRetrieval,
    "project_id": m.LlmProject,
    "document_id": m.LlmKnowledgeDocument,
    "knowledge_document_id": m.LlmKnowledgeDocument,
}
REFERENCE_LISTS = {
    "file_ids": "file_id",
    "artifact_ids": "artifact_id",
    "native_artifact_ids": "artifact_id",
    "agent_handoff_ids": "agent_handoff_id",
    "agent_node_execution_ids": "node_execution_id",
    "evaluation_ids": "evaluation_id",
    "depends_on_task_ids": "task_id",
    "source_message_ids": "message_id",
    "document_ids": "document_id",
}
SOURCE_TYPES = {
    "message": "message_id",
    "artifact": "artifact_id",
    "model_attempt": "attempt_id",
    "evaluation": "evaluation_id",
    "agent_handoff": "agent_handoff_id",
    "tool_call": "tool_call_id",
}


async def require_resource_owner(db: AsyncSession, model, identifier: str, user_id: str) -> None:
    """Resolve an immutable owner through a bounded persisted parent chain; deny missing owners."""
    for _ in range(6):
        record = await db.scalar(select(model).where(inspect(model).primary_key[0] == identifier))
        if record is None:
            raise ResourceNotFoundError("Resource")
        for field in ("owner_user_id", "user_id"):
            owner = getattr(record, field, None)
            if owner is not None:
                if owner != user_id:
                    raise ResourceNotFoundError("Resource")
                return
        parent = next(
            (
                (field, parent_model)
                for field, parent_model in (
                    ("run_id", m.LlmRun),
                    ("session_id", m.LlmSession),
                    ("attempt_id", m.LlmModelAttempt),
                    ("project_id", m.LlmProject),
                    ("workflow_execution_id", m.LlmAgentWorkflowExecution),
                )
                if getattr(record, field, None)
            ),
            None,
        )
        if parent is None:
            break
        field, model = parent
        identifier = getattr(record, field)
    raise ResourceNotFoundError("Resource")


async def validate_resource_references(db, values: dict, user_id: str, *, depth: int = 0) -> None:
    """Check structured locator fields only, never arbitrary user text, schemas or metadata."""
    if depth > 2:
        raise HTTPException(422, "资源引用嵌套过深。")
    for key, value in values.items():
        if key in RESOURCES and isinstance(value, str):
            await require_resource_owner(db, RESOURCES[key], value, user_id)
        elif key in REFERENCE_LISTS and isinstance(value, list):
            if len(value) > 128:
                raise HTTPException(422, "资源引用数量过多。")
            for identifier in value:
                if isinstance(identifier, str):
                    await require_resource_owner(
                        db, RESOURCES[REFERENCE_LISTS[key]], identifier, user_id
                    )
    for key in ("context_source_references", "source_references"):
        references = values.get(key)
        if isinstance(references, dict):
            await validate_resource_references(db, references, user_id, depth=depth + 1)
    if any(
        isinstance(values.get(key), list) and len(values[key]) > 128
        for key in ("parsed_artifacts", "sources")
    ):
        raise HTTPException(422, "资源引用数量过多。")
    for source in (
        values.get("parsed_artifacts", [])
        if isinstance(values.get("parsed_artifacts"), list)
        else []
    ):
        if isinstance(source, dict):
            await validate_resource_references(db, source, user_id, depth=depth + 1)
    sources = values.get("sources")
    if isinstance(sources, list):
        for source in sources:
            if isinstance(source, dict) and source.get("source_type") in SOURCE_TYPES:
                await validate_resource_references(
                    db,
                    {SOURCE_TYPES[source["source_type"]]: source.get("source_resource_id")},
                    user_id,
                    depth=depth + 1,
                )


def owned_list_statement(db: AsyncSession, statement, model):
    """Filter SQL before pagination; unscoped sessions are trusted in-process services only."""
    owner = db.info.get(OWNER_SESSION_KEY)
    if owner is None:
        return statement
    if model in (m.User, m.LlmSession, m.LlmRun, m.LlmMemory, m.LlmKnowledgeDocument):
        return statement.where(model.user_id == owner)
    if model is m.LlmProject:
        return statement.where(model.owner_user_id == owner)
    if model in (m.LlmTask, m.LlmModelAttempt, m.LlmRunArtifact):
        return statement.where(
            model.run_id.in_(select(m.LlmRun.run_id).where(m.LlmRun.user_id == owner))
        )
    raise RuntimeError("Unclassified owned list model")


async def authorize_resource_request(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_database_session)],
) -> None:
    """Require classified service or final-user authority before each versioned business route."""
    route_definition = request.scope["route"]
    route = route_definition.path.removeprefix("/api/v1")
    policy = (request.method, route)
    if policy in INTERNAL_ROUTES:
        await require_internal_api_key(request.headers.get("x-internal-api-key"))
        if request.cookies.get("nexuspilot_session") or request.headers.get("authorization"):
            raise HTTPException(403, "内部管理接口不接受浏览器身份。")
        return
    if policy not in SHARED_ROUTES | OWNED_ROUTES:
        raise HTTPException(403, "接口尚未配置访问权限。")
    principal = await get_workspace_principal(request, db, get_settings())
    db.info[OWNER_SESSION_KEY] = principal.user_id
    if (
        request.method not in {"GET", "HEAD"}
        and principal.authentication_method == "browser_session"
    ):
        validate_authentication_origin(request.headers.get("origin"), get_settings())
        session = await require_owned_login_session(
            db, user_id=principal.user_id, login_session_id=principal.login_session_id
        )
        validate_login_session_csrf(
            login_session=session,
            csrf_cookie=request.cookies.get("nexuspilot_csrf"),
            csrf_header=request.headers.get("x-csrf-token"),
        )
    if policy in SHARED_ROUTES:
        return
    await validate_resource_references(db, request.path_params, principal.user_id)
    # Check every repeated query value rather than trusting first/last parameter precedence.
    for key, value in request.query_params.multi_items():
        await validate_resource_references(db, {key: value}, principal.user_id)
    # Raw file streams and form uploads are not JSON API parameters even if their
    # bytes happen to contain user_id. Follow the route's actual body parser too.
    body_field = route_definition.body_field
    if (
        body_field is not None
        and not isinstance(body_field.field_info, Form)
        and request.method in {"POST", "PATCH", "PUT", "DELETE"}
        and uses_json_body(request.headers.get("content-type"))
    ):
        try:
            payload = await request.json()
        except ValueError:
            return  # FastAPI will reject malformed JSON; path checks have already run.
        if isinstance(payload, dict):
            # Client-supplied storage URIs are a trusted-service-only capability.
            if any(
                payload.get(field)
                for field in (
                    "content_uri",
                    "input_evidence_uri",
                    "raw_request_uri",
                    "raw_response_uri",
                )
            ):
                raise HTTPException(403, "不允许直接指定存储地址。")
            await validate_resource_references(db, payload, principal.user_id)
