"""Opt-in single live-model conversation; synthetic HTTP peer, isolated Nexus storage.

Run only this test with NEXUS_LIVE_CONTEXT_TEST=1 and NEXUS_LIVE_REPORT_DIR set to
a new directory. Existing Nexus credentials and local provider endpoint metadata
are read only. No Assistant imports, business database writes, or model retries.
"""

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from dotenv import dotenv_values
from nexuspilot_models.contracts import ProviderName
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.engine import make_url
from sqlalchemy import select
from test_agent_workflows import INTERNAL_HEADERS, registered_agent_test_model
from test_application_body_processing import HEAD, INPUT
from test_application_tool_loop import app_headers
from test_general_agent import general_payload

from nexuspilot_api.core.config import API_ENV_FILE, Settings, get_settings
from nexuspilot_api.core.dependencies import get_price_catalog, get_provider_registry
from nexuspilot_api.infrastructure.provider_registry import create_price_catalog, create_provider_registry
from nexuspilot_api.main import app
from nexuspilot_api.models import LlmRegisteredModel
from nexuspilot_api.services.provider_endpoint_service import load_enabled_provider_base_urls


@pytest.mark.skipif(os.getenv("NEXUS_LIVE_CONTEXT_TEST") != "1", reason="Requires explicit live-model authorization")
async def test_live_context_only_original_agent(resource_client, test_database_session_factory, monkeypatch):
    """Let a real model propose one context-defined operation and finish from its actual receipt."""
    assert not os.getenv("NEXUSPILOT_TEST_AUTH_MYSQL_URL")
    assert not os.getenv("NEXUSPILOT_TEST_AUTH_MINIO_ENDPOINT")
    output = Path(os.environ["NEXUS_LIVE_REPORT_DIR"])
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    report = {"status": "running", "model_calls": [], "platform_calls": [], "checks": {}}
    operation = "save_context_probe_todo"
    instruction = HEAD.replace("create_todo", operation) + (
        '\n本次业务操作只在上下文声明。不要使用原生工具、搜索、笔记或子Agent。'
        '仅输出JSON，不要Markdown。调用格式为'
        '{"decision":"call","call":{"operation_id":"' + operation + '",'
        '"body":{"title":"...","reminder_at":"...","idempotency_key":"..."},"summary":"..."}}。'
        '终答格式为{"decision":"answer","answer":"...","todo_id":"实际返回编号"}。'
        '业务调用的实际结果回来后，自主判断是否结束。不要捏造编号。'
    )
    calls, saved = report["platform_calls"], {}

    class Handler(BaseHTTPRequestHandler):
        """Emulate a platform that parses full text and saves only synthetic in-memory rows."""

        def log_message(self, *args):
            """Keep authentication and HTTP logging out of test output."""

        def do_POST(self):
            """Validate a context-defined call or accept a final answer without calling AI."""
            envelope = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            calls.append(envelope)
            identity = envelope["execution_id"]
            decision = json.loads(envelope["content"])
            if decision.get("decision") == "call":
                call = decision["call"]
                valid = (call["operation_id"] == operation and call["body"]["title"] == "下班打卡"
                         and call["body"]["reminder_at"] == "2026-11-04T18:10:00+08:00"
                         and bool(call["body"].get("idempotency_key")) and bool(call.get("summary")))
                if not valid or (saved and identity not in saved):
                    result = {"protocol_version": "1", "execution_id": identity,
                              "disposition": "continue", "execution_status": "rejected", "result": None,
                              "error": {"code": "unexpected_call", "message": "本次只允许指定的新待办一次写入", "retryable": False}}
                else:
                    saved.setdefault(identity, {"todo_id": str(uuid4()), "status": "open", **call["body"]})
                    result = {"protocol_version": "1", "execution_id": identity,
                              "disposition": "continue", "execution_status": "succeeded", "error": None,
                              "result": {"applied": True, "todo": saved[identity], "executed_call": call}}
            elif decision.get("decision") == "answer":
                result = {"protocol_version": "1", "execution_id": identity, "disposition": "complete"}
            else:
                self.send_response(422)
                self.end_headers()
                return
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps(result, ensure_ascii=False).encode())

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        values = dotenv_values(API_ENV_FILE)
        real_settings = Settings(_env_file=API_ENV_FILE, model_max_retries=0)
        database_url = values["NEXUSPILOT_DATABASE_URL"]
        assert make_url(database_url).host in {"127.0.0.1", "localhost"}
        backend_env = API_ENV_FILE.parents[3] / "backend/services/assistant-api/.env"
        backend = dotenv_values(backend_env)
        binding = json.loads(backend["ASSISTANT_NEXUS_ROLE_BINDINGS_JSON"])["controller"]
        provider_name, model = ProviderName(binding["provider"]), binding["model"]
        engine = create_async_engine(database_url)
        try:
            async with async_sessionmaker(engine)() as session:
                endpoints = await load_enabled_provider_base_urls(session)
                registered = await session.scalar(select(LlmRegisteredModel).where(
                    LlmRegisteredModel.provider == provider_name,
                    LlmRegisteredModel.model_identifier == model))
                assert registered is not None
                reasoning_profile = {column.name: getattr(registered, column.name)
                    for column in LlmRegisteredModel.__table__.columns
                    if column.name.startswith("reasoning_")}
        finally:
            await engine.dispose()
        report["provider"], report["model"] = provider_name.value, model
        monkeypatch.setattr(get_settings(), "workflow_execution_backend", "inline")
        monkeypatch.setattr(get_settings(), "app_tools_json", SecretStr(json.dumps({"personal_assistant": {
            "inbound_key": "personal_assistant-" + "k" * 32,
            "service_token": "live-context-private-test-token", "operations": {},
            "base_url": f"http://127.0.0.1:{server.server_port}",
            "response_processing_path": "/process", "allow_local_http": True}})))
        async with test_database_session_factory() as db:
            registration = registered_agent_test_model(model)
            registration.provider = provider_name
            for name, value in reasoning_profile.items():
                setattr(registration, name, value)
            db.add(registration)
            await db.commit()
        catalog = await resource_client.post("/api/v1/internal/model-catalog-versions", headers=INTERNAL_HEADERS,
            json={"provider": provider_name.value, "model": model, "catalog_version": "live-context",
                  "source": "isolated-live-context-test", "context_window": 131072,
                  "supports_streaming": True, "tokenizer_name": "utf8_bytes_upper_bound", "tokenizer_version": "v1"})
        assert catalog.status_code == 200
        async with httpx.AsyncClient() as http:
            registry = create_provider_registry(real_settings, http, endpoints)
            provider = registry.resolve(provider_name, model)
            original_generate = provider.generate
            original_stream = provider.stream

            def record_request(request):
                """Capture the actual provider input for either transport without selecting an answer."""
                assert len(report["model_calls"]) < 3
                entry = {"system": request.system_instruction,
                         "messages": [m.model_dump(mode="json") for m in request.messages]}
                report["model_calls"].append(entry)
                return entry

            async def capture(request):
                """Observe non-streaming calls without replacing actual model output."""
                entry = record_request(request)
                response = await original_generate(request)
                entry["response"] = response.model_dump(mode="json", exclude={"raw_response"})
                assert not response.tool_calls, "This probe requires context-only body calls"
                return response

            async def capture_stream(request):
                """Capture streaming input while forwarding unchanged events to the Nexus runtime."""
                entry = record_request(request)
                entry["transport"] = "stream"
                async for event in original_stream(request):
                    yield event

            monkeypatch.setattr(provider, "generate", capture)
            monkeypatch.setattr(provider, "stream", capture_stream)
            app.dependency_overrides[get_provider_registry] = lambda: registry
            app.dependency_overrides[get_price_catalog] = lambda: create_price_catalog(real_settings)
            headers = app_headers("personal_assistant", "live-context-user")
            run = await resource_client.post("/api/v1/app/runs", headers=headers, json={"user_request": INPUT})
            assert run.status_code == 201
            report["run_id"] = run.json()["run_id"]
            payload = general_payload("never", application_response_mode="caller_processed",
                application_protocol_instructions=instruction,
                role_bindings={"controller": {**binding, "timeout_seconds": 90, "max_output_tokens": 1800}},
                max_model_calls=3, wall_time_limit_ms=240000,
                final_output_schema={"type": "object", "required": ["decision", "answer", "todo_id"],
                    "properties": {"decision": {"enum": ["answer"]}, "answer": {"type": "string"}, "todo_id": {"type": "string"}},
                    "additionalProperties": False})
            result = await resource_client.post(f"/api/v1/app/runs/{report['run_id']}/agent-workflows", headers=headers, json=payload)
            report["http_status"] = result.status_code
            report["workflow_result"] = result.json()
            assert result.status_code == 201 and result.json()["status"] == "completed"
        final = json.loads(result.json()["final_output"]["final_text"])
        rounds = report["model_calls"]
        assert result.json()["aggregate_usage"]["tool_call_count"] == 0
        assert len(saved) == 1 and len(rounds) >= 2
        assert final["todo_id"] == next(iter(saved.values()))["todo_id"]
        assert len({c["run_id"] for c in calls}) == len({c["workflow_id"] for c in calls}) == 1
        assert all(r["system"] == rounds[0]["system"] for r in rounds)
        second = rounds[1]["messages"]
        assert second[:len(rounds[0]["messages"])] == rounds[0]["messages"]
        assert sum(m.get("content") == calls[0]["content"] for m in second) == 1
        evidence = next(json.loads(m["content"]) for m in second if '"backend_execution_result"' in (m.get("content") or ""))
        assert evidence["result"]["executed_call"] == json.loads(calls[0]["content"])["call"]
        assert evidence["result"]["todo"] == next(iter(saved.values()))
        assert evidence["call_id"] == calls[0]["execution_id"]
        report["checks"] = {"one_run_and_workflow": True, "one_synthetic_write": True,
                            "original_message_prefix_preserved": True, "full_call_and_result_preserved": True,
                            "model_finished_with_actual_id": True}
        report["status"] = "passed"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
        if report["status"] == "running":
            report["status"] = "failed"
        (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
