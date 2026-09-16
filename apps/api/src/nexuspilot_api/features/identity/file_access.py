"""Enforce final-user access when legacy service endpoints reference new private uploads."""

from fastapi import Request
from sqlalchemy import select

from nexuspilot_api.core.config import get_settings
from nexuspilot_api.core.errors import ResourceNotFoundError
from nexuspilot_api.features.identity.dependencies import get_workspace_principal
from nexuspilot_api.models import (
    LlmAgentWorkflowExecution,
    LlmContextBuild,
    LlmModelAttempt,
    UserFile,
)
from nexuspilot_api.routers.common import DatabaseSessionDependency


async def enforce_private_file_references(request: Request, db: DatabaseSessionDependency):
    """Check private file owners on model, workflow, context and raw Artifact access paths."""

    path = request.url.path.removeprefix("/api/v1/").split("/")
    if path[0] not in {
        "responses",
        "runs",
        "agent-workflows",
        "context-builds",
        "artifacts",
        "attempts",
    }:
        return
    run_id = request.query_params.get("run_id")
    file_ids = []
    if len(path) >= 2:
        if path[0] == "runs":
            run_id = path[1]
        elif path[0] == "artifacts":
            file_ids.append(path[1])
        else:
            model = {
                "agent-workflows": LlmAgentWorkflowExecution,
                "context-builds": LlmContextBuild,
                "attempts": LlmModelAttempt,
            }.get(path[0])
            if model:
                record = await db.get(model, path[1])
                if record:
                    run_id = record.run_id
    if request.method == "POST" and request.headers.get("content-type", "").startswith(
        "application/json"
    ):
        try:
            payload = await request.json()
        except ValueError:
            payload = {}  # Preserve path-based checks for non-JSON legacy artifact uploads.
        if isinstance(payload, dict):
            # A body must not override the resource named in the URL.
            if run_id is None:
                run_id = payload.get("run_id")
            references = payload.get(
                "context_source_references", payload.get("source_references", {})
            )
            if isinstance(references, dict):
                parsed = references.get("parsed_artifacts")
                if isinstance(parsed, list):
                    file_ids.extend(
                        ref.get("artifact_id") for ref in parsed if isinstance(ref, dict)
                    )
                artifacts = references.get("artifact_ids")
                if isinstance(artifacts, list):
                    file_ids.extend(artifacts)
    files = list(
        (
            await db.scalars(
                select(UserFile).where(
                    UserFile.file_id.in_([value for value in file_ids if isinstance(value, str)])
                    | ((UserFile.run_id == run_id) if isinstance(run_id, str) else False),
                )
            )
        ).all()
    )
    if not files:
        return
    from nexuspilot_api.routers.user_files import file_principal

    principal = await get_workspace_principal(request, db, get_settings())
    await file_principal(request, db, principal)
    if any(file.user_id != principal.user_id for file in files):
        raise ResourceNotFoundError("File")
