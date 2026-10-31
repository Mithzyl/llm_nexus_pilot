"""Restrict configured MCP grants for a user, without accepting arbitrary URLs or credentials."""

import asyncio

import httpx
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from nexuspilot_api.core.errors import InvalidRequestError, ResourceConflictError
from nexuspilot_api.features.identity.models.mcp_preferences import MCPUserConnection
from nexuspilot_api.models.base import new_id, utc_now
from nexuspilot_api.models.identity import User
from nexuspilot_api.services.agent_file_tool_service import ToolOperationError
from nexuspilot_api.services.mcp_tools import MCPTransport, connections


def selected_config(config, policy):
    """Return a restricted copy only while the saved approved definitions still match."""
    if policy is None:
        return config.model_copy(update={"permission_revision": 0})
    if (
        not policy.enabled
        or policy.config_version != config.version
        or not policy.approved_tools_json
    ):
        return None
    if any(
        name not in config.tools or config.tools[name].model_dump() != definition
        for name, definition in policy.approved_tools_json.items()
    ):
        return None
    return config.model_copy(
        update={
            "tools": {name: config.tools[name] for name in policy.approved_tools_json},
            "permission_revision": policy.revision,
        }
    )


async def effective_connections(db, owner):
    """Read committed policy in a fresh session so an in-flight transaction observes revocation."""
    configs = {alias: config for alias, config in connections().items() if owner in config.user_ids}
    async with AsyncSession(bind=db.bind) as fresh:
        user = await fresh.get(User, owner)
        if user is None or not user.is_active:
            return {}
        rows = await fresh.scalars(
            select(MCPUserConnection).where(MCPUserConnection.owner_user_id == owner)
        )
        policies = {row.connection_alias: row for row in rows}
        result = {}
        for alias, config in configs.items():
            selected = selected_config(config, policies.get(alias))
            if selected is not None:
                result[alias] = selected
        return result


async def list_connections(db, owner):
    """Expose safe aliases and approved tools only; never include URL, tokens or other owners."""
    rows = await db.scalars(
        select(MCPUserConnection)
        .where(MCPUserConnection.owner_user_id == owner)
        .execution_options(populate_existing=True)
    )
    policies = {row.connection_alias: row for row in rows}
    items = []
    for alias, config in connections().items():
        if owner not in config.user_ids:
            continue
        policy = policies.get(alias)
        selected = selected_config(config, policy)
        items.append(
            {
                "connection": alias,
                "config_version": config.version,
                "revision": policy.revision if policy else 0,
                "enabled": selected is not None,
                "review_required": bool(policy and policy.enabled and selected is None),
                "discovery_status": policy.discovery_status if policy else None,
                "discovered_at": policy.discovered_at if policy else None,
                "tools": [
                    {
                        "name": name,
                        "description": tool.description,
                        "selected": name
                        in (policy.approved_tools_json if policy else config.tools),
                        "input_schema": tool.input_schema,
                    }
                    for name, tool in config.tools.items()
                ],
            }
        )
    return {"items": items}


async def save_connection(
    db, owner, *, connection, config_version, expected_revision, enabled, tools
):
    """Persist an explicit subset under an owner lock; never broaden administrator permission."""
    config = connections().get(connection)
    if config is None or owner not in config.user_ids:
        raise InvalidRequestError("MCP connection is unavailable")
    if config_version != config.version:
        raise ResourceConflictError("MCP approval changed; reload connection")
    if (
        len(set(tools)) != len(tools)
        or not set(tools) <= config.tools.keys()
        or enabled
        and not tools
    ):
        raise InvalidRequestError("Select only approved MCP tools")
    user = await db.scalar(select(User).where(User.user_id == owner).with_for_update())
    if user is None or not user.is_active:
        raise InvalidRequestError("MCP connection is unavailable")
    row = await db.scalar(
        select(MCPUserConnection)
        .where(
            MCPUserConnection.owner_user_id == owner,
            MCPUserConnection.connection_alias == connection,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if expected_revision != (row.revision if row else 0):
        raise ResourceConflictError("MCP settings changed; reload connection")
    if row is None:
        row = MCPUserConnection(owner_user_id=owner, connection_alias=connection, revision=0)
        db.add(row)
    row.config_version, row.enabled = config_version, enabled
    row.approved_tools_json = {name: config.tools[name].model_dump() for name in tools}
    row.discovery_status, row.discovered_at = None, None
    row.revision += 1
    await db.commit()
    return {"connection": connection, "revision": row.revision, "enabled": row.enabled}


async def discover_connection(db, owner, *, connection, config_version, expected_revision):
    """Refresh only approved definitions; discard results if permission changes while waiting."""
    approved = await effective_connections(db, owner)
    config = approved.get(connection)
    if config is None:
        raise InvalidRequestError("MCP connection is unavailable or disabled")
    if config.version != config_version or config.permission_revision != expected_revision:
        raise ResourceConflictError("MCP settings changed; reload connection")
    if expected_revision == 0:
        saved = await save_connection(
            db,
            owner,
            connection=connection,
            config_version=config_version,
            expected_revision=0,
            enabled=True,
            tools=list(config.tools),
        )
        expected_revision = saved["revision"]
        config = (await effective_connections(db, owner)).get(connection)
        if config is None or config.permission_revision != expected_revision:
            raise ResourceConflictError("MCP settings changed; reload connection")
    status, result, failure = "verified", None, None
    async with httpx.AsyncClient() as client:
        pending = asyncio.create_task(MCPTransport(config, client).discover(new_id()))
        try:
            async with asyncio.timeout(30):
                while True:
                    done, _ = await asyncio.wait({pending}, timeout=0.1)
                    current = await effective_connections(db, owner)
                    if current.get(connection) != config:
                        raise ResourceConflictError("MCP settings changed during discovery")
                    if done:
                        result = pending.result()
                        break
        except ToolOperationError as error:
            status, failure = error.code, error
        except TimeoutError:
            status, failure = "mcp_discovery_timeout", ToolOperationError("mcp_discovery_timeout")
        finally:
            if not pending.done():
                pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
    changed = await db.execute(
        update(MCPUserConnection)
        .where(
            MCPUserConnection.owner_user_id == owner,
            MCPUserConnection.connection_alias == connection,
            MCPUserConnection.revision == expected_revision,
            MCPUserConnection.enabled.is_(True),
        )
        .values(discovery_status=status, discovered_at=utc_now())
    )
    if changed.rowcount != 1:
        await db.rollback()
        raise ResourceConflictError("MCP settings changed during discovery")
    await db.commit()
    if failure:
        raise failure
    return {"connection": connection, "revision": expected_revision, "tools": result["tools"]}
