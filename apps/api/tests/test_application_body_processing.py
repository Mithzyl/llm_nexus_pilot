"""Exercise caller-owned business execution with a real local HTTP peer and fixed models."""

import asyncio
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from nexuspilot_models.contracts import ModelResponse
from pydantic import SecretStr
from test_agent_workflows import (
    ScriptedAgentProvider,
    register_default_agent_test_model,  # noqa: F401
    scripted_registry,
)  # noqa: F401
from test_application_tool_loop import app_headers
from test_general_agent import (  # noqa: F401
    general_model_catalog,
    general_payload,
    unit_review_text,
)

from nexuspilot_api.core.config import get_settings
from nexuspilot_api.core.dependencies import get_provider_registry
from nexuspilot_api.main import app

HEAD = """只执行当前用户请求的动作。历史用于理解背景，不代表重做历史操作。
不同事项和待办必须按真实编号区分，不仅凭标题。AI调用提议不是成功证据，后端结果才是依据。
判断下一步先核对执行结果与当前要求；相同成功创建不得再次提出。创建成功不等于待办完成。
当前要求全部完成后回答，不再重复创建；参数不明确时询问，不猜测。
保存提醒时间不代表通知已送达，不承诺未实现的推送。
本次从模拟平台OpenAPI选出的唯一业务操作：create_todo POST /api/v1/todos。
required: title,idempotency_key；可选reminder_at为带时区时间。idempotency_key由平台预先提供。
success_evidence: applied=true且返回todo编号。普通待办通知尚未接入。
decision: call/answer/ask_user/search。卡片仅text/fields，值引用实际结果，状态由后端填写。
不加载图片、旅游时间线或编辑草稿定义。"""
INPUT = json.dumps(
    {
        "history": [
            {
                "task_reference": {"matter_id": "matter-A", "todo_id": "todo-A"},
                "scope": "历史资料，不属于本次待执行动作",
                "messages": [
                    {"origin": "user", "text": "新增一个弄清楚优惠销售品的待办"},
                    {"origin": "assistant", "text": "记好了，待办已经添加：弄清楚优惠销售品。"},
                ],
                "backend_evidence": {
                    "creation_status": "succeeded",
                    "source": "已保存的后端执行记录",
                },
            },
            {
                "task_reference": {"matter_id": "matter-B", "todo_id": "todo-B"},
                "scope": "历史资料，不属于本次待执行动作",
                "messages": [
                    {
                        "origin": "user",
                        "text": ("帮我存一个支取转存步骤：1.核对合同号、签报和客户。"
                                 "2.核对合同号A金额。"),
                    },
                    {"origin": "assistant", "text": "存好了，支取转存的两步检查已经记成一条待办。"},
                ],
                "backend_evidence": {
                    "creation_status": "succeeded",
                    "source": "已保存的后端执行记录",
                },
            },
        ],
        "current_request": {
            "request_id": "request-C",
            "origin": "user",
            "received_at": "2026-11-04T17:59:17+08:00",
            "timezone": "Asia/Shanghai",
            "text": "创建一个6点10分提醒的待办，下班打卡",
            "explicit_target": "新增，不修改A/B",
            "idempotency_key": "request-C-create",
        },
        "execution_progress": {
            "completed_operations": [],
            "pending_requirement": "创建下班打卡并保存18:10提醒",
        },
    },
    ensure_ascii=False,
)


def contains_original_input(value):
    """Locate the exact caller-supplied history/current-request text inside model input wrappers."""
    if value == INPUT:
        return True
    if isinstance(value, dict):
        return any(contains_original_input(item) for item in value.values())
    if isinstance(value, list):
        return any(contains_original_input(item) for item in value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except ValueError:
            return False
        return contains_original_input(parsed) if parsed != value else False
    return False


PROPOSAL = json.dumps(
    {
        "decision": "call",
        "call": {
            "operation_id": "create_todo",
            "body": {
                "title": "下班打卡",
                "reminder_at": "2026-11-04T18:10:00+08:00",
                "idempotency_key": "request-C-create",
            },
            "summary": "新增下班打卡并保存18:10提醒时间",
        },
    },
    ensure_ascii=False,
)


@pytest.fixture
def body_peer(monkeypatch):
    """Save one synthetic todo per stable execution identity without registering a Nexus tool."""
    calls, saved = [], {}

    class Handler(BaseHTTPRequestHandler):
        """Serve only this synthetic caller protocol, without logging credentials."""

        def log_message(self, *args):
            """Suppress request logging for the private fixture."""

        def do_POST(self):
            """Return actual synthetic business facts or accept a final candidate."""
            value = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            calls.append(value)
            assert value["external_user_id"] == "body-user"
            identity = value["execution_id"]
            if identity not in saved:
                if value["content"] == PROPOSAL:
                    saved[identity] = {
                        "protocol_version": "1",
                        "execution_id": identity,
                        "disposition": "continue",
                        "execution_status": "succeeded",
                        "error": None,
                        "result": {
                            "applied": True,
                            "todo": {"todo_id": "todo-C", "status": "open"},
                            "executed_call": json.loads(PROPOSAL)["call"],
                        },
                    }
                else:
                    saved[identity] = {
                        "protocol_version": "1",
                        "execution_id": identity,
                        "disposition": "complete",
                    }
            self.send_response(200)
            self.end_headers()
            if self.path == "/slow":
                time.sleep(1)
            try:
                self.wfile.write(
                    b"not-json" if self.path == "/invalid" else json.dumps(saved[identity]).encode()
                )
            except (BrokenPipeError, ConnectionResetError):
                pass  # Client cancellation does not undo the synthetic saved operation.

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(get_settings(), "workflow_execution_backend", "inline")
    monkeypatch.setattr(
        get_settings(),
        "app_tools_json",
        SecretStr(
            json.dumps(
                {
                    "personal_assistant": {
                        "inbound_key": "personal_assistant-" + "k" * 32,
                        "service_token": "body-private-token",
                        "base_url": f"http://127.0.0.1:{server.server_port}",
                        "operations": {},
                        "response_processing_path": "/process",
                        "allow_local_http": True,
                    }
                }
            )
        ),
    )
    try:
        yield calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.parametrize("policy", ["never", "always", "on_verification_failure"])
async def test_body_result_continues_original_agent(
    resource_client, body_peer, policy, test_database_session_factory
):
    """Keep fixed instructions, original proposal and real result across two main model calls."""

    class Provider(ScriptedAgentProvider):
        """Capture real model requests and return deterministic proposal, answer and review."""

        def __init__(self):
            """Track main and review requests independently."""
            super().__init__()
            self.main = []

        async def generate(self, request):
            """Assert business state comes from the platform, never the historical AI answer."""
            if request.metadata.get("agent_role") == "reviewer":
                text = unit_review_text(request, "supported", "Checked actual execution evidence")
            else:
                self.main.append(request)
                if len(self.main) == 1:
                    text = PROPOSAL
                else:
                    messages = [m.content or "" for m in request.messages]
                    assert PROPOSAL in messages
                    assert any("todo-C" in m and "backend_execution_result" in m for m in messages)
                    assert all(HEAD in (r.system_instruction or "") for r in self.main)
                    assert self.main[0].system_instruction == request.system_instruction
                    assert any(contains_original_input(m) for m in messages)
                    assert not any(m.role == "tool" for m in request.messages)
                    evidence = next(
                        json.loads(m) for m in messages if "backend_execution_result" in m
                    )
                    assert evidence["result"]["executed_call"] == json.loads(PROPOSAL)["call"]
                    assert evidence["call_id"] == body_peer[0]["execution_id"]
                    assert evidence["response_id"] == body_peer[0]["response_id"]
                    text = "已创建todo-C并保存18:10提醒；待办仍未完成，不能承诺通知送达。"
            return ModelResponse(
                text=text,
                input_tokens=1,
                output_tokens=1,
                latency_ms=1,
                finish_reason="stop",
                raw_response={"fixture": True},
            )

    provider = Provider()
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)
    headers = app_headers("personal_assistant", "body-user")
    run = await resource_client.post(
        "/api/v1/app/runs", headers=headers, json={"user_request": INPUT}
    )
    assert run.status_code == 201, run.text
    payload = general_payload(
        policy, application_response_mode="caller_processed", application_protocol_instructions=HEAD
    )
    path = f"/api/v1/app/runs/{run.json()['run_id']}/agent-workflows"
    result = await resource_client.post(path, headers=headers, json=payload)
    assert result.status_code == 201, result.text
    assert result.json()["status"] == "completed", result.text
    assert "todo-C" in result.json()["final_output"]["final_text"]
    assert len(provider.main) == 2 and len(body_peer) == 2
    # Read an already committed result as a resumed loop would: never dispatch it again.
    from types import SimpleNamespace

    from nexuspilot_api.infrastructure.object_storage import get_object_storage
    from nexuspilot_api.services.application_body_processing import process_application_body

    async with test_database_session_factory() as db:
        service = SimpleNamespace(
            model_invocation_service=SimpleNamespace(
                db_session=db, storage=app.dependency_overrides[get_object_storage]()
            )
        )
        original = body_peer[0]
        restored = await process_application_body(
            service,
            SimpleNamespace(run_id=original["run_id"]),
            None,
            SimpleNamespace(id=original["response_id"], output_text=PROPOSAL, finish_reason="stop"),
            1,
        )
        assert restored["execution_id"] == original["execution_id"]
        assert restored["result"]["applied"] is True
        assert len(body_peer) == 2
    assert len({c["workflow_id"] for c in body_peer}) == 1
    assert len({c["run_id"] for c in body_peer}) == 1
    replay = await resource_client.post(path, headers=headers, json=payload)
    assert replay.status_code in {200, 201}
    assert len(provider.main) == 2 and len(body_peer) == 2


@pytest.mark.parametrize("invalid_result", [False, True])
async def test_repeated_proposal_or_unknown_result_never_reexecutes(
    resource_client, body_peer, monkeypatch, invalid_result
):
    """Stop repeated proposals and ambiguous platform responses without repeating writes."""

    class Provider(ScriptedAgentProvider):
        """Keep returning the same proposed operation to exercise deterministic stopping."""

        async def generate(self, request):
            """Return a complete proposal, not an assertion about a successful save."""
            return ModelResponse(
                text=PROPOSAL,
                input_tokens=1,
                output_tokens=1,
                latency_ms=1,
                finish_reason="stop",
                raw_response={},
            )

    if invalid_result:
        config = json.loads(get_settings().app_tools_json.get_secret_value())
        config["personal_assistant"]["response_processing_path"] = "/invalid"
        monkeypatch.setattr(get_settings(), "app_tools_json", SecretStr(json.dumps(config)))
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(Provider())
    headers = app_headers("personal_assistant", "body-user")
    run = await resource_client.post(
        "/api/v1/app/runs", headers=headers, json={"user_request": INPUT}
    )
    path = f"/api/v1/app/runs/{run.json()['run_id']}/agent-workflows"
    payload = general_payload(
        "never",
        application_response_mode="caller_processed",
        application_protocol_instructions=HEAD,
    )
    result = await resource_client.post(path, headers=headers, json=payload)
    assert result.status_code == 201, result.text
    assert result.json()["status"] == ("outcome_unknown" if invalid_result else "failed"), (
        result.text
    )
    assert len(body_peer) == 1
    replay = await resource_client.post(path, headers=headers, json=payload)
    assert replay.status_code in {200, 201} and len(body_peer) == 1


async def test_cancel_while_caller_is_processing(resource_client, body_peer, monkeypatch):
    """Cancel the original Run while HTTP is pending without another model or platform call."""

    class Provider(ScriptedAgentProvider):
        """Only one proposal is expected before explicit cancellation."""

        async def generate(self, request):
            """Return a synthetic call proposal with no provider side effects."""
            assert not body_peer
            return ModelResponse(
                text=PROPOSAL,
                input_tokens=1,
                output_tokens=1,
                latency_ms=1,
                finish_reason="stop",
                raw_response={},
            )

    config = json.loads(get_settings().app_tools_json.get_secret_value())
    config["personal_assistant"]["response_processing_path"] = "/slow"
    monkeypatch.setattr(get_settings(), "app_tools_json", SecretStr(json.dumps(config)))
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(Provider())
    headers = app_headers("personal_assistant", "body-user")
    run = (
        await resource_client.post(
            "/api/v1/app/runs", headers=headers, json={"user_request": INPUT}
        )
    ).json()
    pending = asyncio.create_task(
        resource_client.post(
            f"/api/v1/app/runs/{run['run_id']}/agent-workflows",
            headers=headers,
            json=general_payload(
                "never",
                application_response_mode="caller_processed",
                application_protocol_instructions=HEAD,
            ),
        )
    )
    try:
        for _ in range(200):
            if body_peer:
                break
            await asyncio.sleep(0.01)
        assert len(body_peer) == 1
        cancelled = await resource_client.post(
            f"/api/v1/app/runs/{run['run_id']}/cancel", headers=headers
        )
        assert cancelled.status_code == 200
        result = await asyncio.wait_for(pending, 3)
        assert result.json()["status"] == "cancelled", result.text
        assert len(body_peer) == 1
    finally:
        if not pending.done():
            pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)
