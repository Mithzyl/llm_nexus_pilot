"""Expose durable questions as tools; model output cannot answer or approve its own request."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from nexuspilot_api.core.config import get_settings
from nexuspilot_api.services.tool_catalog import RegisteredTool, ToolCatalog


class InteractionArguments(BaseModel):
    """Ask a bounded question without caller-controlled identity or permission fields."""

    model_config = ConfigDict(extra="forbid")
    kind: Literal["approval", "input"]
    question: str = Field(min_length=1, max_length=4000)


class InteractionResult(BaseModel):
    """Describe an actual user reply, not an authorization to bypass resource checks."""

    model_config = ConfigDict(extra="forbid")
    interaction_id: str
    kind: Literal["approval", "input"]
    approved: bool | None = None
    text: str | None = None
    expired: bool = False
    grants_additional_permissions: Literal[False] = False


def install_interaction_tools(tools):
    """Advertise human waiting only under a resumable Worker with private body encryption."""
    name = "request_user_input"
    retained = tuple(tool for tool in tools.catalog.entries if tool.name != name)
    eligible = bool(
        (tools.db.info.get("quick_response_claim") or tools.db.info.get("workflow_execution_claim"))
        and get_settings().memory_encryption_key
    )
    entries = (
        (
            RegisteredTool(
                name=name,
                version="1.0.0",
                description="Ask the user for missing input or approval and wait for their reply. "
                "Use kind=input for a question, kind=approval for a yes/no decision. "
                "Approval does not grant tool, file, or memory permissions.",
                arguments=InteractionArguments,
                output=InteractionResult,
                required_capabilities=frozenset({"read"}),
                initially_loaded=True,
                keywords=("user", "approval", "clarification"),
                timeout_seconds=None,
            ),
        )
        if eligible
        else ()
    )
    tools.catalog = ToolCatalog((*retained, *entries))
    if eligible:
        tools.loaded_versions[name] = "1.0.0"
    else:
        tools.loaded_versions.pop(name, None)
