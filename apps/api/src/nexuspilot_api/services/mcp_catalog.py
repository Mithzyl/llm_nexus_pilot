"""Build independent approved MCP definitions while retaining the legacy bridge tools."""

from copy import deepcopy
from uuid import uuid4

from pydantic import RootModel, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nexuspilot_api.features.identity.models.mcp_catalog import MCPToolBinding
from nexuspilot_api.models import LlmRun, User
from nexuspilot_api.services.agent_file_tool_service import FileToolResult, ToolOperationError
from nexuspilot_api.services.mcp_tools import closed_input_schema
from nexuspilot_api.services.tool_catalog import RegisteredTool
from nexuspilot_api.services.tool_schema import validate_value


def argument_model(schema):
    """Validate the actual remote argument object while exposing its pinned schema unchanged."""
    closed = closed_input_schema(schema)

    class MCPArguments(RootModel[dict]):
        """Keep remote parameters in the root object, without synthetic connection fields."""

        @model_validator(mode="after")
        def validate_remote(self):
            """Reject unsupported fields or types before any remote request."""
            validate_value(closed, self.root)
            return self

        @classmethod
        def model_json_schema(cls, *args, **kwargs):
            """Return a fresh schema so caller annotations cannot mutate pinned approval."""
            return deepcopy(closed)

    return MCPArguments


async def catalog_entries(db, owner, configs):
    """Allocate stable random aliases without committing the caller's transaction."""
    if not configs:
        return ()
    entries = []
    async with AsyncSession(bind=db.bind, expire_on_commit=False) as session:
        user = await session.scalar(select(User).where(User.user_id == owner).with_for_update())
        if user is None or not user.is_active:
            raise ToolOperationError("mcp_not_allowed", status="permission_denied")
        for connection, config in configs.items():
            for name, tool in config.tools.items():
                row = await session.scalar(
                    select(MCPToolBinding).where(
                        MCPToolBinding.owner_user_id == owner,
                        MCPToolBinding.connection_alias == connection,
                        MCPToolBinding.config_version == config.version,
                        MCPToolBinding.permission_revision == config.permission_revision,
                        MCPToolBinding.remote_tool_name == name,
                    )
                )
                definition = tool.model_dump()
                if row is None:
                    row = MCPToolBinding(
                        tool_alias="mcp_remote_" + uuid4().hex,
                        owner_user_id=owner,
                        connection_alias=connection,
                        config_version=config.version,
                        permission_revision=config.permission_revision,
                        remote_tool_name=name,
                        definition_json=definition,
                    )
                    session.add(row)
                elif row.definition_json != definition:
                    raise ToolOperationError("mcp_definition_changed")
                entries.append(
                    RegisteredTool(
                        name=row.tool_alias,
                        version=config.version,
                        description=f"{connection} / {name}: {tool.description}"[:2048],
                        arguments=argument_model(tool.input_schema),
                        output=FileToolResult,
                        required_capabilities=frozenset({"mcp_read"}),
                        keywords=(connection, name),
                        timeout_seconds=30,
                        execution_type="remote_http",
                        owner_application_id="native",
                        canonical_name=f"mcp:{connection}:{name}",
                    )
                )
        await session.commit()
    return tuple(entries)


async def resolve_binding(db, owner, alias, configs):
    """Recheck owner, connection revision and exact definition before execution or history reuse."""
    async with AsyncSession(bind=db.bind, expire_on_commit=False) as session:
        row = await session.get(MCPToolBinding, alias)
        config = configs.get(row.connection_alias) if row and row.owner_user_id == owner else None
        if (
            config is None
            or config.version != row.config_version
            or config.permission_revision != row.permission_revision
            or row.remote_tool_name not in config.tools
            or config.tools[row.remote_tool_name].model_dump() != row.definition_json
        ):
            raise ToolOperationError("mcp_grant_revoked", status="permission_denied")
        return row


async def execute_bound_mcp(db, task_id, attempt, record, alias, arguments):
    """Resolve the saved binding and reuse remote validation and cancellation."""
    from nexuspilot_api.services.mcp_tools import (
        MCPCallArguments,
        approved_connections,
        execute_mcp,
    )

    configs = await approved_connections(db, attempt.run_id, task_id)
    run = await db.get(LlmRun, attempt.run_id)
    binding = await resolve_binding(db, run.user_id if run else None, alias, configs)
    return await execute_mcp(
        db,
        task_id,
        attempt,
        record,
        "mcp_call_tool",
        MCPCallArguments(
            connection=binding.connection_alias,
            version=binding.config_version,
            tool_name=binding.remote_tool_name,
            arguments=arguments.root,
        ),
    )
