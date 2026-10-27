"""Verify bounded schemas and MCP wire behavior without a live server or model."""

import asyncio
import json

import httpx
import pytest
from test_agent_workflows import register_default_agent_test_model  # noqa: F401
from test_general_agent import general_model_catalog  # noqa: F401

from nexuspilot_api.services.agent_file_tool_service import ToolOperationError
from nexuspilot_api.services.mcp_tools import MCPConnection, MCPTransport
from nexuspilot_api.services.tool_schema import check_schema, validate_value


@pytest.mark.parametrize("case", ["success", "wrong_result", "revoked", "unvalidated"])
async def test_dynamic_python_lifecycle(client, monkeypatch, test_database_session_factory, case):
    """Run draft/validate/register/invoke in the real loop with a fixed model and fake container."""
    from nexuspilot_models.contracts import ModelResponse, ToolCall
    from test_agent_workflows import scripted_registry
    from test_api import create_test_run
    from test_general_agent import GeneralProvider, general_payload

    from nexuspilot_api.core.config import get_settings
    from nexuspilot_api.core.dependencies import get_provider_registry
    from nexuspilot_api.main import app
    from nexuspilot_api.services import python_tool_service

    run = await create_test_run(client)
    monkeypatch.setattr(get_settings(), "python_runtime_socket", "/tmp/test-python.sock")
    monkeypatch.setattr(get_settings(), "python_execution_user_ids", [run["user_id"]])
    monkeypatch.setattr(get_settings(), "dynamic_python_enabled", True)
    executions = []

    async def execute(socket, request):
        """Return strict JSON output while asserting code reached the existing executor."""
        executions.append(request)
        assert "def run" in request["command"]["stdin"]
        return {
            "status": "completed",
            "exit_code": 0,
            "container_removed": True,
            "stdout": "99" if case == "wrong_result" else "6",
            "stderr": "",
            "stdout_truncated": False,
            "stderr_truncated": False,
            "files": {},
        }

    monkeypatch.setattr(python_tool_service, "execute_python_request", execute)

    class Provider(GeneralProvider):
        """Drive the lifecycle deterministically without a paid model call."""

        async def generate(self, request):
            """Consume saved IDs rather than manufacturing a validation or registration."""
            messages = [json.loads(m.content) for m in request.messages if m.role.value == "tool"]
            if not messages:
                name, arguments = (
                    "create_tool_draft",
                    {
                        "description": "Multiply n by two",
                        "source": "def run(data):\n return data['n']*2",
                        "input_schema": {
                            "type": "object",
                            "properties": {"n": {"type": "integer"}},
                            "required": ["n"],
                            "additionalProperties": False,
                        },
                        "output_schema": {"type": "integer"},
                        "sample_input": {"n": 3},
                        "expected_output": 6,
                    },
                )
            elif len(messages) == 1:
                draft_id = messages[0]["result"]["draft_id"]
                name, arguments = (
                    ("register_task_tool", {"validation_id": draft_id})
                    if case == "unvalidated"
                    else ("validate_tool_draft", {"draft_id": draft_id})
                )
            elif len(messages) == 2 and case in {"wrong_result", "unvalidated"}:
                assert messages[-1]["status"] != "completed"
                return ModelResponse(
                    text="Rejected", finish_reason="stop", latency_ms=1, raw_response={}
                )
            elif len(messages) == 2:
                name, arguments = (
                    "register_task_tool",
                    {
                        "validation_id": messages[-1]["result"]["validation_id"],
                    },
                )
            elif len(messages) == 3:
                if case == "revoked":
                    monkeypatch.setattr(get_settings(), "dynamic_python_enabled", False)
                name, arguments = (
                    "call_task_tool",
                    {
                        "registration_id": messages[-1]["result"]["registration_id"],
                        "arguments": {"n": 3},
                    },
                )
            else:
                assert messages[-1]["status"] == "completed", messages[-1]
                assert messages[-1]["result"]["value"] == 6
                return ModelResponse(
                    text="Result: 6", finish_reason="stop", latency_ms=1, raw_response={}
                )
            return ModelResponse(
                tool_calls=[ToolCall(id=f"call-{len(messages)}", name=name, arguments=arguments)],
                finish_reason="tool_calls",
                latency_ms=1,
                raw_response={},
            )

    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(Provider())
    response = await client.post(
        f"/api/v1/runs/{run['run_id']}/agent-workflows", json=general_payload("never")
    )
    assert response.status_code == 201, response.text
    assert response.json()["status"] == ("failed" if case == "revoked" else "completed"), (
        response.text
    )
    assert (
        len(executions) == {"success": 2, "wrong_result": 1, "revoked": 1, "unvalidated": 0}[case]
    )
    if case == "success":
        from sqlalchemy import select

        from nexuspilot_api.models import LlmModelAttempt, LlmModelToolCall
        from nexuspilot_api.services.dynamic_python_tools import saved_call

        async with test_database_session_factory() as db:
            registration = await db.scalar(
                select(LlmModelToolCall).where(
                    LlmModelToolCall.tool_name == "register_task_tool",
                )
            )
            attempt = await db.get(LlmModelAttempt, registration.attempt_id)
            saved = await saved_call(
                db, run["run_id"], attempt.task_id, registration.tool_call_id, "register_task_tool"
            )
            assert saved.output_json["result"]["registration_id"] == saved.tool_call_id
            with pytest.raises(ToolOperationError, match="task_tool_reference_unavailable"):
                await saved_call(
                    db, run["run_id"], "another-task", saved.tool_call_id, "register_task_tool"
                )
        # A repeated start replays the finished workflow, not its Python executions.
        replay = await client.post(
            f"/api/v1/runs/{run['run_id']}/agent-workflows", json=general_payload("never")
        )
        assert replay.status_code == 200
        assert len(executions) == 2


def test_supported_schema_rejects_unknown_constraints_and_invalid_values():
    """Never silently ignore schema constraints or accept bool as an integer."""
    schema = {
        "type": "object",
        "properties": {"n": {"type": "integer"}},
        "required": ["n"],
        "additionalProperties": False,
    }
    check_schema(schema)
    validate_value(schema, {"n": 3})
    for value in ({"n": True}, {"n": 3, "owner": "other"}, {}):
        with pytest.raises(ValueError):
            validate_value(schema, value)
    with pytest.raises(ValueError):
        check_schema({"type": "string", "pattern": "x"})
    validate_value({"type": "integer"}, 10**400)
    with pytest.raises(ValueError):
        validate_value({"type": "number"}, float("nan"))


@pytest.mark.parametrize("value", [3, "中文", "'); raise RuntimeError('not code') #"])
def test_python_wrapper_transports_json_without_code_interpolation(value):
    """Run a fixed trusted fixture in a child Python; parameter text must stay data."""
    import subprocess
    import sys

    from nexuspilot_api.services.dynamic_python_tools import program

    source = "def run(data):\n print('diagnostic')\n return data['value']"
    result = subprocess.run(
        [sys.executable, "-I", "-c", program(source, {"value": value})],
        capture_output=True, text=True, timeout=5, check=True,
    )
    assert json.loads(result.stdout) == value
    assert result.stderr.strip() == "diagnostic"


def connection():
    """Supply synthetic server approval and a credential that must not enter tool results."""
    return MCPConnection.model_validate(
        {
            "url": "https://approved.example/mcp",
            "token": "test-private-token",
            "user_ids": ["owner"],
            "tools": {
                "lookup": {
                    "description": "Read a value",
                    "input_schema": {
                        "type": "object",
                        "properties": {},
                        "additionalProperties": False,
                    },
                }
            },
        }
    )


@pytest.mark.parametrize("case", ["success", "remote_error", "schema_changed", "wrong_id"])
async def test_mcp_initialization_discovery_and_call(case):
    """Use real JSON-RPC serialization over mock HTTP; preserve explicit remote failures."""
    calls = []

    async def respond(request):
        """Assert protocol ordering, private auth and strict matching response identifiers."""
        payload = json.loads(request.content)
        calls.append(payload)
        assert request.headers["Authorization"] == "Bearer test-private-token"
        method = payload["method"]
        if method == "notifications/initialized":
            return httpx.Response(202)
        if method == "initialize":
            result = {
                "protocolVersion": "2025-03-26",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "fixture", "version": "1"},
            }
        elif method == "tools/list":
            schema = connection().tools["lookup"].input_schema
            result = {
                "tools": [
                    {
                        "name": "lookup",
                        "inputSchema": schema if case != "schema_changed" else {"type": "string"},
                    }
                ]
            }
        else:
            assert payload["params"] == {"name": "lookup", "arguments": {}}
            result = {
                "content": [{"type": "text", "text": "found"}],
                "isError": case == "remote_error",
            }
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": "wrong" if case == "wrong_id" else payload["id"],
                "result": result,
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        adapter = MCPTransport(connection(), client)
        if case == "success":
            result = await adapter.call("lookup", {}, "execution-id")
            assert result["content"][0]["text"] == "found"
        else:
            with pytest.raises(ToolOperationError):
                await adapter.call("lookup", {}, "execution-id")
    assert calls[0]["method"] == "initialize"
    if case != "wrong_id":
        assert calls[1]["method"] == "notifications/initialized"


@pytest.mark.parametrize(
    "case", ["pagination", "cursor_repeat", "timeout", "redirect", "binary", "oversize"]
)
async def test_mcp_protocol_failures_and_pagination(case):
    """Require supported text results and bounded pagination; never follow a credential redirect."""
    pages = []

    async def respond(request):
        """Simulate protocol failures without opening a network connection."""
        payload = json.loads(request.content)
        method = payload["method"]
        if case == "timeout":
            raise httpx.ReadTimeout("fixture timeout")
        if case == "redirect":
            return httpx.Response(302, headers={"Location": "https://unapproved.example"})
        if case == "oversize":
            return httpx.Response(200, text="x" * (256 * 1024 + 1))
        if method == "notifications/initialized":
            return httpx.Response(202)
        if method == "initialize":
            result = {"protocolVersion": "2025-03-26", "capabilities": {"tools": {}}}
        elif method == "tools/list":
            pages.append(payload["params"])
            if len(pages) == 1 or case == "cursor_repeat":
                result = {"tools": [], "nextCursor": "next"}
            else:
                result = {
                    "tools": [
                        {"name": "lookup", "inputSchema": connection().tools["lookup"].input_schema}
                    ]
                }
        else:
            result = (
                {"content": [{"type": "image", "data": "x"}]}
                if case == "binary"
                else {
                    "content": [],
                }
            )
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": payload["id"], "result": result})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        adapter = MCPTransport(connection(), client)
        if case == "pagination":
            assert (await adapter.call("lookup", {}, "exec"))["content"] == []
            assert pages == [{}, {"cursor": "next"}]
        else:
            with pytest.raises(ToolOperationError):
                await adapter.call("lookup", {}, "exec")


async def test_native_extension_scope_rejects_app_and_inactive_user():
    """Native MCP approval cannot transfer implicitly to an App identity or disabled account."""
    from types import SimpleNamespace

    from nexuspilot_api.features.identity.models.applications import RunApplicationContext
    from nexuspilot_api.models import LlmRun, LlmTask, TaskStatus, User
    from nexuspilot_api.services.mcp_tools import native_task_owner

    records = {
        LlmRun: SimpleNamespace(user_id="owner", status=SimpleNamespace(value="running")),
        LlmTask: SimpleNamespace(run_id="run", status=TaskStatus.RUNNING),
        User: SimpleNamespace(is_active=True),
        RunApplicationContext: None,
    }

    class Database:
        """Return only synthetic ownership records for the pure authorization decision."""

        async def get(self, kind, key, **kwargs):
            """Mirror record lookup without network or application credentials."""
            return records[kind]

    db = Database()
    assert await native_task_owner(db, "run", "task") == "owner"
    records[RunApplicationContext] = object()
    assert await native_task_owner(db, "run", "task") is None
    records[RunApplicationContext] = None
    records[User].is_active = False
    assert await native_task_owner(db, "run", "task") is None


async def test_dynamic_validation_cancel_uses_existing_python_stop(client, monkeypatch):
    """Cancelling validation interrupts the container transport and never registers a tool."""
    from nexuspilot_models.contracts import ModelResponse, ToolCall
    from test_agent_workflows import scripted_registry
    from test_api import create_test_run
    from test_general_agent import GeneralProvider, general_payload

    from nexuspilot_api.core.config import get_settings
    from nexuspilot_api.core.dependencies import get_provider_registry
    from nexuspilot_api.main import app
    from nexuspilot_api.services import python_tool_service

    run = await create_test_run(client)
    monkeypatch.setattr(get_settings(), "python_runtime_socket", "/tmp/fixture-python.sock")
    monkeypatch.setattr(get_settings(), "python_execution_user_ids", [run["user_id"]])
    monkeypatch.setattr(get_settings(), "dynamic_python_enabled", True)
    started, stopped = asyncio.Event(), asyncio.Event()

    async def execute(socket, request):
        """Represent an in-flight container that acknowledges local cancellation."""
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    monkeypatch.setattr(python_tool_service, "execute_python_request", execute)

    class Provider(GeneralProvider):
        """Create and validate a draft; prohibit generation after the explicit stop."""

        async def generate(self, request):
            """Use real persisted draft identity with a synthetic model."""
            previous = [json.loads(m.content) for m in request.messages if m.role.value == "tool"]
            assert len(previous) <= 1
            if not previous:
                name, args = (
                    "create_tool_draft",
                    {
                        "description": "Fixture",
                        "source": "def run(data):\n return 1",
                        "input_schema": {
                            "type": "object",
                            "properties": {},
                            "additionalProperties": False,
                        },
                        "output_schema": {"type": "integer"},
                        "sample_input": {},
                        "expected_output": 1,
                    },
                )
            else:
                name, args = "validate_tool_draft", {"draft_id": previous[0]["result"]["draft_id"]}
            return ModelResponse(
                tool_calls=[ToolCall(id=f"call-{len(previous)}", name=name, arguments=args)],
                finish_reason="tool_calls",
                latency_ms=1,
                raw_response={},
            )

    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(Provider())
    pending = asyncio.create_task(
        client.post(
            f"/api/v1/runs/{run['run_id']}/agent-workflows",
            json=general_payload("never", stream=True),
        )
    )
    try:
        await asyncio.wait_for(started.wait(), 5)
        summary = (await client.get(f"/api/v1/runs/{run['run_id']}/agent-workflow")).json()
        workflow = summary["workflow_execution_id"]
        assert (await client.post(f"/api/v1/agent-workflows/{workflow}/cancel")).status_code == 200
        await asyncio.wait_for(pending, 5)
        result = (await client.get(f"/api/v1/agent-workflows/{workflow}/result")).json()
        assert result["status"] == "cancelled" and result["final_output"] is None
        assert stopped.is_set()
    finally:
        if not pending.done():
            pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)


async def test_mcp_cancel_notifies_pending_call():
    """Cancelling a tool request cancels HTTP and sends the original JSON-RPC request ID."""
    entered = asyncio.Event()
    notifications = []

    async def respond(request):
        """Keep only the actual call pending, recording best-effort cancellation."""
        data = json.loads(request.content)
        if data["method"] == "notifications/cancelled":
            notifications.append(data)
            return httpx.Response(202)
        entered.set()
        await asyncio.Event().wait()

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        adapter = MCPTransport(connection(), client)
        pending = asyncio.create_task(adapter.request("tools/call", {}, "call-1"))
        await entered.wait()
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
    assert notifications[0]["params"]["requestId"] == "call-1"


def test_mcp_sse_preserves_fragmented_unicode_and_ignores_progress():
    """An incomplete UTF-8 character is buffered, not rejected or silently dropped."""
    frame = ('data: {"jsonrpc":"2.0","id":"x","result":{"text":"中文"}}\n\n').encode()
    offset = frame.index("中".encode()) + 1
    assert MCPTransport.sse_reply(frame[:offset], "x") is None
    assert MCPTransport.sse_reply(frame, "x")["result"]["text"] == "中文"
    assert MCPTransport.sse_reply(b'data: {"method":"progress"}\n\n', "x") is None


@pytest.mark.parametrize("case", ["success", "foreign_owner", "revoked", "cancel"])
async def test_mcp_in_general_agent(client, monkeypatch, case):
    """Exercise saved identity, tool messages and revocation inside the existing Agent loop."""
    from nexuspilot_models.contracts import ModelResponse, ToolCall
    from pydantic import SecretStr
    from test_agent_workflows import scripted_registry
    from test_api import create_test_run
    from test_general_agent import GeneralProvider, general_payload

    from nexuspilot_api.core.config import get_settings
    from nexuspilot_api.core.dependencies import get_provider_registry
    from nexuspilot_api.main import app

    run = await create_test_run(client)
    config = connection().model_dump(mode="json")
    config["token"] = "test-private-token"
    config["user_ids"] = ["foreign" if case == "foreign_owner" else run["user_id"]]
    monkeypatch.setattr(
        get_settings(), "mcp_connections_json", SecretStr(json.dumps({"demo": config}))
    )
    entered = asyncio.Event()
    cancelled = asyncio.Event()
    calls = []

    async def invoke(self, name, arguments, execution_id):
        """Replace the external service only; authorization and Tool Call persistence stay real."""
        calls.append(execution_id)
        if case in {"revoked", "cancel"}:
            entered.set()
            if case == "revoked":
                monkeypatch.setattr(get_settings(), "mcp_connections_json", SecretStr("{}"))
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        return {"content": [{"type": "text", "text": "fixture result"}]}

    monkeypatch.setattr(MCPTransport, "call", invoke)

    class Provider(GeneralProvider):
        """Use an advertised MCP bridge and confirm normal content reaches the next turn."""

        async def generate(self, request):
            """Attempt the call once; the catalog rejects it for an unapproved owner."""
            previous = [m for m in request.messages if m.role.value == "tool"]
            if not previous:
                return ModelResponse(
                    tool_calls=[
                        ToolCall(
                            id="mcp-call",
                            name="mcp_call_tool",
                            arguments={
                                "connection": "demo",
                                "version": "1.0.0",
                                "tool_name": "lookup",
                                "arguments": {},
                            },
                        )
                    ],
                    finish_reason="tool_calls",
                    latency_ms=1,
                    raw_response={},
                )
            result = json.loads(previous[-1].content)
            if case == "foreign_owner":
                assert result["status"] == "permission_denied"
            elif case == "revoked":
                assert result["status"] == "permission_denied"
                assert result["result"] is None
            elif case == "success":
                assert result["result"]["content"][0]["text"] == "fixture result"
            return ModelResponse(
                text="Fixture finished", finish_reason="stop", latency_ms=1, raw_response={}
            )

    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(Provider())
    pending = asyncio.create_task(
        client.post(
            f"/api/v1/runs/{run['run_id']}/agent-workflows",
            json=general_payload("never", stream=case == "cancel"),
        )
    )
    if case == "cancel":
        await asyncio.wait_for(entered.wait(), 5)
        summary = (await client.get(f"/api/v1/runs/{run['run_id']}/agent-workflow")).json()
        workflow_id = summary["workflow_execution_id"]
        response = await client.post(f"/api/v1/agent-workflows/{workflow_id}/cancel")
        assert response.status_code == 200, response.text
    response = await asyncio.wait_for(pending, 10)
    if case == "cancel":
        response = await client.get(f"/api/v1/agent-workflows/{workflow_id}/result")
        assert response.status_code == 200
        assert response.json()["status"] == "cancelled"
        assert cancelled.is_set()
        return
    assert response.status_code == 201, response.text
    assert response.json()["status"] == "completed", response.text
    assert len(calls) == (0 if case == "foreign_owner" else 1)
    if case in {"cancel", "revoked"}:
        assert cancelled.is_set()
