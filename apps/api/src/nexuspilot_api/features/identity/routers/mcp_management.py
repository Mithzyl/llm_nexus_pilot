"""Browser-only management of an existing approved MCP connection subset."""

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from nexuspilot_api.features.identity.routers.account_linking import browser_owner
from nexuspilot_api.routers.common import DatabaseSessionDependency
from nexuspilot_api.services.agent_file_tool_service import ToolOperationError
from nexuspilot_api.services.mcp_management import (
    discover_connection,
    list_connections,
    save_connection,
)

router = APIRouter(tags=["mcp-connections"])


class MCPSelection(BaseModel):
    """Select only a configured alias and approved tools, never an owner or endpoint."""

    model_config = ConfigDict(extra="forbid")
    connection: str = Field(min_length=1, max_length=64)
    config_version: str = Field(pattern=r"^\d+\.\d+\.\d+$", max_length=32)
    expected_revision: int = Field(ge=0)
    enabled: bool
    tools: list[str] = Field(max_length=32)


class MCPDiscovery(BaseModel):
    """Request discovery for a displayed revision, without granting new tools."""

    model_config = ConfigDict(extra="forbid")
    connection: str = Field(min_length=1, max_length=64)
    config_version: str = Field(pattern=r"^\d+\.\d+\.\d+$", max_length=32)
    expected_revision: int = Field(ge=0)


@router.post("/me/mcp-connections/discover")
async def post_mcp_discovery(
    payload: MCPDiscovery, request: Request, response: Response, db: DatabaseSessionDependency
):
    """Refresh approved remote definitions only after a browser user explicitly requests it."""
    principal = await browser_owner(request, db)
    response.headers["Cache-Control"] = "no-store"
    try:
        return await discover_connection(db, principal.user_id, **payload.model_dump())
    except ToolOperationError as error:
        raise HTTPException(
            502, detail={"code": error.code, "message": "MCP discovery failed"}
        ) from None


@router.get("/me/mcp-connections")
async def get_mcp_connections(request: Request, response: Response, db: DatabaseSessionDependency):
    """Return only the current browser user's safe connection metadata."""
    principal = await browser_owner(request, db)
    response.headers["Cache-Control"] = "no-store"
    return await list_connections(db, principal.user_id)


@router.post("/me/mcp-connections")
async def post_mcp_connection(
    payload: MCPSelection, request: Request, response: Response, db: DatabaseSessionDependency
):
    """Use authenticated browser identity and enclosing CSRF checks to restrict runtime grants."""
    principal = await browser_owner(request, db)
    response.headers["Cache-Control"] = "no-store"
    return await save_connection(db, principal.user_id, **payload.model_dump())
