"""Memory tools shared by Responses and general Agent; authority comes from saved Runs."""

import json
import re
from typing import Literal

from pydantic import Field
from sqlalchemy.ext.asyncio import async_sessionmaker

from nexuspilot_api.features.memory.models.product_memory import (
    MemoryRunPermission,
    UserMemoryEntry,
)
from nexuspilot_api.features.memory.services.product_memory import MemoryFailure, memory_for_run
from nexuspilot_api.models import LlmRun
from nexuspilot_api.services.agent_file_tool_service import (
    EmptyArguments,
    FileToolResult,
    ToolOperationError,
)
from nexuspilot_api.services.tool_catalog import RegisteredTool, ToolCatalog


class ScopeArguments(EmptyArguments):
    """Select personal or the server-bound current project, never an arbitrary project ID."""

    scope: Literal["personal", "project"] = "personal"


class QueryArguments(ScopeArguments):
    """Bound query results without accepting identity or permission overrides."""

    query: str = Field(default="", max_length=128)
    offset: int = Field(default=0, ge=0, le=10000)
    shared_offset: int = Field(default=0, ge=0, le=10000)
    limit: int = Field(default=10, ge=1, le=20)


class SaveArguments(ScopeArguments):
    """Provide the exact explicitly requested text; confirmation cannot be supplied by a model."""

    text: str = Field(min_length=1, max_length=4000)
    sensitive: bool = False


class TargetArguments(ScopeArguments):
    """Target a record and version already returned in this scope."""

    entry_id: str = Field(min_length=36, max_length=36)
    expected_version: int = Field(ge=1)


class CorrectArguments(TargetArguments):
    """Carry an exact replacement without asserting user consent."""

    text: str = Field(min_length=1, max_length=4000)
    sensitive: bool = False


class SettingArguments(ScopeArguments):
    """Change only the current entry or current project's inherited setting."""

    enabled: bool
    expected_version: int = Field(ge=0)


MEMORY_TOOLS = tuple(
    RegisteredTool(
        name=name,
        version="1.0.0",
        description=description,
        arguments=schema,
        output=FileToolResult,
        required_capabilities=frozenset({"memory_use"}),
        initially_loaded=True,
        risk_level="read_only"
        if name in {"query_memory", "get_memory_settings"}
        else "memory_write",
    )
    for name, schema, description in (
        (
            "query_memory",
            QueryArguments,
            "Read relevant personal/current-project memories on demand. "
            "Sensitive results require user disclosure confirmation. disclosure_required means "
            "saved memory exists but this conversation is not authorized; "
            "ask the user to authorize it in the UI, never claim there is no memory. "
            "Answer naturally from authorized facts "
            "without internal correction narratives or file citation aliases. "
            "shared_items contains separately approved cross-entry fields. "
            "Use next_shared_offset as shared_offset to continue shared pagination. "
            "Do not fetch all memory routinely.",
        ),
        (
            "save_memory",
            SaveArguments,
            "Save the user's exact explicit remember request. Do not use for "
            "inferred preferences. Reworded content becomes a candidate "
            "requiring user confirmation; "
            "inspect result.status and never report a candidate as active. "
            "Report saved only after success; "
            "sensitive saving needs confirmation.",
        ),
        (
            "propose_memory",
            SaveArguments,
            "Propose a memory candidate, never an active fact. "
            "The user must confirm before it can be used.",
        ),
        (
            "confirm_memory",
            TargetArguments,
            "Confirm one ordinary candidate only when the user explicitly says 确认记住 followed "
            "by its exact text or ID. Sensitive confirmation requires a trusted user action.",
        ),
        (
            "correct_memory",
            CorrectArguments,
            "Correct one uniquely identified memory on the user's "
            "explicit request, with its current version. Ambiguity requires clarification.",
        ),
        (
            "forget_memory",
            TargetArguments,
            "Forget a uniquely requested memory. This does not delete "
            "original chats, project files, calendar events or supplier copies.",
        ),
        (
            "get_memory_settings",
            ScopeArguments,
            "Read effective memory settings; entry default is on, "
            "project inherits or is disabled. Closing does not delete memories.",
        ),
        (
            "set_memory_settings",
            SettingArguments,
            "Follow an explicit user request to close or reopen "
            "memory in this entry/current project. Never enable it yourself in order to save.",
        ),
    )
)
MEMORY_NAMES = {tool.name for tool in MEMORY_TOOLS}


async def install_memory_tools(tools, run_id):
    """Refresh eligibility from persisted authentication, including after Worker restart."""
    permission = await tools.db.get(MemoryRunPermission, run_id, populate_existing=True)
    allowed = bool(permission and permission.allowed)
    if allowed:
        try:
            await memory_for_run(tools.db, tools.storage, run_id)
        except MemoryFailure:
            allowed = False
    retained = tuple(t for t in tools.catalog.entries if t.name not in MEMORY_NAMES)
    tools.catalog = ToolCatalog((*retained, *(MEMORY_TOOLS if allowed else ())))
    tools.capabilities = (tools.capabilities - {"memory_use"}) | (
        {"memory_use"} if allowed else set()
    )
    if allowed:
        tools.loaded_versions.update({t.name: t.version for t in MEMORY_TOOLS})


async def execute_memory(tools, attempt, record, name, arguments):
    """Check user authority; stage rewritten save text for explicit confirmation."""
    factory = async_sessionmaker(tools.db.bind, class_=type(tools.db), expire_on_commit=False)
    async with factory() as db:
        db.info.update(tools.db.info)
        try:
            permission = await db.get(MemoryRunPermission, attempt.run_id, populate_existing=True)
            run = await db.get(LlmRun, attempt.run_id, populate_existing=True)
            if (
                not permission
                or not permission.allowed
                or run.status.value not in {"pending", "running"}
            ):
                raise MemoryFailure("memory_not_allowed")
            service = await memory_for_run(db, tools.storage, attempt.run_id, arguments.scope)
            service.require_running = True
            prompt = run.user_request.strip().rstrip("。.!！")
            if name == "get_memory_settings":
                return await service.settings()
            if name == "query_memory":
                management = bool(
                    re.search(r"记忆|记得|偏好|确认记住|memory|remember", prompt, re.I)
                )
                result = await service.query(
                    automatic=not management,
                    for_model=True,
                    query=arguments.query,
                    offset=arguments.offset,
                    limit=arguments.limit,
                    include_candidates=management,
                )
                from nexuspilot_api.features.memory.services.sharing import shared_query_page

                shared = await shared_query_page(service, offset=arguments.shared_offset,
                                                 limit=arguments.limit)
                result["shared_items"] = [
                    {key: value for key, value in item.items()
                     if key not in {"text", "app_data", "typed_data"}}
                    for item in shared["items"]
                ]
                result["next_shared_offset"] = shared["next_offset"]
                # Persist only locators. Rehydrate authorized text immediately before generation,
                # so forgotten content cannot be recovered from a saved Tool Call or checkpoint.
                result["items"] = [
                    {key: value for key, value in item.items() if key not in {"text", "app_data", "typed_data"}}
                    for item in result["items"]
                ]
                return {**result, "memory_scope": arguments.scope, "management": management}
            request = {"action": name.removesuffix("_memory"), "key": record.tool_call_id}
            if name in {"save_memory", "propose_memory"}:
                if name == "save_memory":
                    match = re.fullmatch(
                        r"(?:请)?(?:记住|记一下|remember)\s*[:：]?\s*(.+)", prompt, re.I
                    )
                    if not match:
                        raise MemoryFailure("memory_clarification_required")
                    if match[1] != arguments.text.strip().rstrip("。.!！"):
                        # Rewriting is not consent: preserve the proposed text as an inactive
                        # candidate under the same tool operation key, never a second active save.
                        request["action"] = "propose"
                request.update(text=arguments.text, sensitive=arguments.sensitive)
            elif name == "set_memory_settings":
                expected = (
                    r"(?:请)?(?:重新)?(?:开启|打开|启用)记忆"
                    if arguments.enabled
                    else r"(?:请)?关闭记忆"
                )
                if not re.fullmatch(expected, prompt):
                    raise MemoryFailure("memory_clarification_required")
                request.update(
                    action="settings",
                    enabled=arguments.enabled,
                    expected_version=arguments.expected_version,
                )
            else:
                # Resolve targets against current scoped bodies, never arbitrary IDs.
                match = (
                    re.fullmatch(r"(?:请)?(?:把)?(.+?)(?:改成|改为)(.+)", prompt)
                    if name == "correct_memory"
                    else re.fullmatch(
                        r"(?:请)?确认记住(.+)" if name == "confirm_memory" else r"(?:请)?忘记(.+)",
                        prompt,
                    )
                )
                if not match:
                    raise MemoryFailure("memory_clarification_required")
                target = match[1].removeprefix("我的").removeprefix("我")
                matches = []
                for offset in range(0, 200, 50):
                    page = await service.query(offset=offset, limit=50)
                    matches.extend(
                        item
                        for item in page["items"]
                        if (target == item["entry_id"] or target in item.get("text", ""))
                    )
                    if page["next_offset"] is None:
                        break
                if len(matches) != 1 or matches[0]["entry_id"] != arguments.entry_id:
                    raise MemoryFailure("memory_clarification_required")
                request.update(
                    entry_id=arguments.entry_id, expected_version=arguments.expected_version
                )
                if name == "correct_memory":
                    if match[2] != arguments.text.strip().rstrip("。.!！"):
                        raise MemoryFailure("memory_clarification_required")
                    request.update(text=arguments.text, sensitive=arguments.sensitive)
            return await service.mutate(request)
        except MemoryFailure as error:
            raise ToolOperationError(str(error)) from None


async def sanitize_memory_history(tools, run_id, records, messages):
    """Recheck in a fresh transaction so MySQL's earlier read snapshot cannot revive old text."""
    if not any(r.tool_name == "query_memory" and r.status == "completed" for r in records):
        return
    factory = async_sessionmaker(tools.db.bind, class_=type(tools.db), expire_on_commit=False)
    async with factory() as db:
        db.info.update(tools.db.info)
        await restore_current_memory_messages(db, tools.storage, run_id, records, messages)


async def restore_current_memory_messages(db, storage, run_id, records, messages):
    """Read only still-authorized versions into model messages, leaving audit objects unchanged."""
    by_call = {
        record.provider_tool_call_id: record
        for record in records
        if record.tool_name == "query_memory" and record.status == "completed"
    }
    for message in messages:
        if message.role.value != "tool" or message.tool_call_id not in by_call:
            continue
        record = by_call[message.tool_call_id]
        service = await memory_for_run(
            db, storage, run_id, record.input_json.get("scope", "personal")
        )
        payload = json.loads(message.content)
        result = payload.get("result") or {}
        enabled = (await service.settings())["enabled"]
        filtered = []
        for item in result.get("items", []):
            row = await db.get(UserMemoryEntry, item["entry_id"], populate_existing=True)
            scope = await service.scope(service.project_id)
            if (
                row
                and scope
                and row.scope_id == scope.scope_id
                and (
                    row.status == "active"
                    or (row.status == "candidate" and result.get("management"))
                )
                and row.version == item["version"]
                and (enabled or result.get("management"))
            ):
                restored = {
                    key: value for key, value in item.items() if key not in {"text", "app_data", "typed_data"}
                }
                from nexuspilot_api.features.memory.services.disclosures import is_disclosed

                if not row.sensitive or await is_disclosed(db, row, run_id, service.entry):
                    body = await service.read(row.body_uri)
                    restored["text"] = body["text"]
                    if body.get("app_data") is not None:
                        restored["app_data"] = body["app_data"]
                    if body.get("typed_data") is not None:
                        restored["typed_data"] = body["typed_data"]
                    restored.pop("disclosure_required", None)
                else:
                    restored["disclosure_required"] = True
                filtered.append(restored)
        result["items"] = filtered
        from nexuspilot_api.features.memory.services.sharing import read_shared

        shared = []
        for item in result.get("shared_items", []):
            current = await read_shared(service, for_model=True, grant_id=item["grant_id"])
            shared.extend(value for value in current if value["version"] == item["version"]
                          and value["grant_version"] == item["grant_version"])
        result["shared_items"] = shared
        result["history_revalidated"] = True
        payload["result"] = result
        message.content = json.dumps(payload, ensure_ascii=False)


def memory_checkpoint_message(message):
    """Serialize memory queries as references, never as another plaintext memory snapshot."""
    encoded = message.model_dump(mode="json")
    if message.role.value == "tool" and message.tool_name == "query_memory":
        payload = json.loads(message.content)
        result = payload.get("result") or {}
        result["items"] = [
            {key: value for key, value in item.items() if key not in {"text", "app_data", "typed_data"}}
            for item in result.get("items", [])
        ]
        result["shared_items"] = [
            {key: value for key, value in item.items()
             if key not in {"text", "app_data", "typed_data"}}
            for item in result.get("shared_items", [])
        ]
        payload["result"] = result
        encoded["content"] = json.dumps(payload, ensure_ascii=False)
    return encoded


async def check_memory_external_disclosure(db, run_id, tool):
    """A Provider-only sensitive-memory grant never authorizes forwarding it to network tools."""
    from sqlalchemy import select
    from nexuspilot_api.features.memory.services.disclosures import disclosure_condition

    network = tool.execution_type == "remote_http" or tool.name in {
        "web_search",
        "web_fetch",
        "mcp_call_tool",
    }
    if network and await db.scalar(
        select(UserMemoryEntry.entry_id)
        .where(
            disclosure_condition(run_id),
            UserMemoryEntry.status != "forgotten",
        )
        .limit(1)
    ):
        raise ToolOperationError(
            "memory_external_disclosure_not_confirmed", status="permission_denied"
        )
