"""Verify opt-in, persisted read-only tools using isolated SQL and scripted models."""

import hashlib
import json

import pytest
from nexuspilot_models.contracts import ModelResponse, ToolCall
from sqlalchemy import select
from test_agent_workflows import (
    ScriptedAgentProvider,
    register_default_agent_test_model,  # noqa: F401 — reuse the model catalog fixture
    scripted_registry,
    workflow_payload,
)
from test_api import create_test_run

from nexuspilot_api.core.dependencies import get_provider_registry
from nexuspilot_api.main import app
from nexuspilot_api.models import LlmModelToolCall


@pytest.fixture(autouse=True)
def expose_unexpected_application_errors(client):
    """Keep unexpected API exceptions diagnosable instead of hiding them behind HTTP 500 text."""
    client._transport.raise_app_exceptions = True


class FileToolProvider(ScriptedAgentProvider):
    """Request a real catalog read, consume its result, then finish the assigned task."""

    def __init__(self, *, tool_name="list_run_files", arguments=None, repeat=False):
        """Choose a deterministic tool request without invoking a real Provider."""
        super().__init__()
        self.tool_name = tool_name
        self.arguments = arguments or {}
        self.repeat = repeat

    async def generate(self, request):
        """Describe deferred tools before reading; retain original permission/error checks."""
        if request.metadata["agent_node_key"] == "worker_execution":
            previous = [message for message in request.messages if message.role.value == "tool"]
            deferred = self.tool_name in {
                "get_file_outline",
                "read_file_table",
                "search_file_content",
            }
            if deferred and not previous and request.tools:
                self.requests.append(request)
                return ModelResponse(
                    tool_calls=[
                        ToolCall(
                            id="load-1",
                            name="describe_tools",
                            arguments={"name": self.tool_name, "version": "1.0.0"},
                        )
                    ],
                    finish_reason="tool_calls",
                    input_tokens=30,
                    output_tokens=10,
                    latency_ms=1,
                    raw_response={"fixture": "describe-file-tool"},
                )
            if (
                not previous
                or self.repeat
                or (deferred and previous[-1].tool_name == "describe_tools")
            ):
                self.requests.append(request)
                return ModelResponse(
                    tool_calls=[
                        ToolCall(id="read-1", name=self.tool_name, arguments=self.arguments)
                    ],
                    finish_reason="tool_calls",
                    input_tokens=30,
                    output_tokens=10,
                    latency_ms=1,
                    raw_response={"fixture": "file-tool"},
                )
            assert json.loads(previous[-1].content)["status"] in {
                "completed",
                "permission_denied",
                "failed",
                "timed_out",
            }
        response = await super().generate(request)
        if (
            request.metadata["agent_node_key"] == "controller_planning"
            and "tool_guidance" in request.messages[-1].content
        ):
            output = response.structured_output
            output["tasks"][0]["max_model_calls"] = 3
            response = response.model_copy(update={"text": json.dumps(output)})
        return response

    def _node_output(self, node_key, request):
        """Build worker evidence from the actual tool result, not a fixed last prompt."""
        if node_key == "worker_execution" and request.messages[-1].role.value == "tool":
            return {
                "summary": "Read completed: " + request.messages[-1].content,
                "confirmed_facts": [request.messages[-1].content],
                "decisions": [],
                "remaining_work": [],
                "risks": [],
                "unknowns": [],
            }
        return super()._node_output(node_key, request)


@pytest.mark.parametrize(
    ("name", "arguments", "status"),
    [
        ("list_run_files", {}, "completed"),
        ("run_command", {"command": "echo forbidden"}, "permission_denied"),
        ("list_run_files", {"user_id": "somebody-else"}, "failed"),
    ],
)
async def test_tool_workflow_persists_result_and_continues(
    client, test_database_session_factory, name, arguments, status
):
    """Only the explicit tool profile executes tools; all attempts and refusals survive reload."""
    run = await create_test_run(client)
    provider = FileToolProvider(tool_name=name, arguments=arguments)
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)
    response = await client.post(
        f"/api/v1/runs/{run['run_id']}/agent-workflows",
        json=workflow_payload(execution_profile="tool_enabled_v1"),
    )
    assert response.status_code == 201, response.text
    result = response.json()
    assert result["status"] == "completed", json.dumps(result.get("error") or result)
    worker = next(n for n in result["nodes"] if n["node_key"].startswith("worker_execution."))
    assert worker["usage"]["model_call_count"] == 2
    assert worker["usage"]["tool_call_count"] == 1
    assert len(worker["evidence"]["model_attempt_ids"]) == 2
    assert len(worker["evidence"]["tool_call_ids"]) == 1
    assert worker["output"]["tool_results"][0]["status"] == status
    worker_requests = [
        r for r in provider.requests if r.metadata["agent_node_key"] == "worker_execution"
    ]
    assert {tool.name for tool in worker_requests[0].tools} == {
        "list_run_files",
        "read_file_content",
        "search_file_content",
        "search_tools",
        "describe_tools",
    }
    assert worker_requests[0].output_schema is None
    assert worker_requests[0].json_object_output is False
    assert worker_requests[1].messages[-1].tool_call_id == "read-1"
    async with test_database_session_factory() as db:
        records = list(await db.scalars(select(LlmModelToolCall)))
        assert len(records) == 1
        assert records[0].status == status
        assert records[0].tool_version == (None if name == "run_command" else "1.0.0")
    refreshed = await client.get(
        f"/api/v1/agent-workflows/{result['workflow_execution_id']}/result"
    )
    assert refreshed.json() == result


async def test_model_only_profile_still_refuses_tool_calls(client, test_database_session_factory):
    """Existing model-only requests never acquire file or command permissions implicitly."""
    run = await create_test_run(client)
    provider = FileToolProvider()
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)
    result = (
        await client.post(
            f"/api/v1/runs/{run['run_id']}/agent-workflows",
            json=workflow_payload(),
        )
    ).json()
    assert result["status"] == "failed"
    async with test_database_session_factory() as db:
        assert list(await db.scalars(select(LlmModelToolCall))) == []


async def test_worker_lists_then_reads_file_before_submitting_answer(client):
    """Attachments automatically enable discovery/read rounds without a user tool switch."""
    from test_conversation_file_context import prepare_conversation_file

    _, _, file, turn = await prepare_conversation_file(client)

    class DiscoverThenReadProvider(FileToolProvider):
        """Choose the second tool from the first tool's actual file listing."""

        async def generate(self, request):
            """Read the discovered file before permitting the scripted final worker object."""
            previous = [m for m in request.messages if m.role.value == "tool"]
            if request.metadata["agent_node_key"] == "worker_execution" and len(previous) == 1:
                discovered = json.loads(previous[0].content)["result"]["files"][0]["file_id"]
                assert discovered == file["file_id"]
                self.requests.append(request)
                return ModelResponse(
                    tool_calls=[
                        ToolCall(
                            id="read-2", name="read_file_content", arguments={"file_id": discovered}
                        )
                    ],
                    finish_reason="tool_calls",
                    input_tokens=40,
                    output_tokens=10,
                    latency_ms=1,
                    raw_response={"fixture": "second-read"},
                )
            return await super().generate(request)

    provider = DiscoverThenReadProvider()
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)
    result = (
        await client.post(
            f"/api/v1/runs/{turn['run']['run_id']}/agent-workflows",
            json=workflow_payload(),
        )
    ).json()
    assert result["status"] == "completed", result
    worker = next(n for n in result["nodes"] if n["node_key"].startswith("worker_execution."))
    assert worker["usage"]["model_call_count"] == 3
    assert worker["usage"]["tool_call_count"] == 2
    assert "UNIQUE_FILE_TEXT_2718" in worker["output"]["structured_output"]["summary"]
    requests = [r for r in provider.requests if r.metadata["agent_node_key"] == "worker_execution"]
    assert [m.tool_call_id for m in requests[-1].messages if m.role.value == "tool"] == [
        "read-1",
        "read-2",
    ]
    replay = await client.post(
        f"/api/v1/runs/{turn['run']['run_id']}/agent-workflows",
        json=workflow_payload(),
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["workflow_execution_id"] == result["workflow_execution_id"]


async def test_repeated_tool_request_stops_without_unbounded_calls(client):
    """Repeated identical tool work terminates truthfully rather than looping until timeout."""
    run = await create_test_run(client)
    provider = FileToolProvider(repeat=True)
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)
    result = (
        await client.post(
            f"/api/v1/runs/{run['run_id']}/agent-workflows",
            json=workflow_payload(execution_profile="tool_enabled_v1"),
        )
    ).json()
    assert result["status"] == "failed"
    assert len(provider.requests) <= 4


@pytest.mark.parametrize(
    "tool_name", ["read_file_content", "get_file_outline", "search_file_content", "read_file_table"]
)
async def test_file_tools_read_real_uploaded_parse_and_preserve_context(
    client, test_database_session_factory, tool_name
):
    """Read persisted extraction and retain base Context plus exact tool request history."""
    from test_conversation_file_context import prepare_conversation_file

    from nexuspilot_api.infrastructure.object_storage import get_object_storage
    from nexuspilot_api.models import LlmModelAttempt

    owner, session, file, turn = await prepare_conversation_file(client)
    content = b"UNIQUE_FILE_TEXT_2718"
    if tool_name == "read_file_table":
        from test_user_files import reserve_file

        content = b"name,value\nalpha,12\n"
        file = (await reserve_file(client, content, "table.csv")).json()
        assert (
            await client.put(f"/api/v1/files/{file['file_id']}/content", content=content)
        ).status_code == 200
        turn = (
            await client.post(
                f"/api/v1/sessions/{session['session_id']}/turns",
                json={
                    "user_id": owner,
                    "content_text": "Read this table",
                    "file_ids": [file["file_id"]],
                },
            )
        ).json()
    args = {"file_id": file["file_id"]}
    if tool_name == "search_file_content":
        args["query"] = "UNIQUE"
    provider = FileToolProvider(tool_name=tool_name, arguments=args)
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)
    response = await client.post(
        f"/api/v1/runs/{turn['run']['run_id']}/agent-workflows",
        json=workflow_payload(execution_profile="tool_enabled_v1"),
    )
    assert response.status_code == 201, response.text
    assert response.json()["status"] == "completed", response.text
    storage = app.dependency_overrides[get_object_storage]()
    async with test_database_session_factory() as db:
        record = await db.scalar(
            select(LlmModelToolCall).where(LlmModelToolCall.tool_name == tool_name)
        )
        assert record.status == "completed"
        assert record.output_json["result"]["file_id"] == file["file_id"]
        assert record.output_json["result"]["source_hash"] == hashlib.sha256(content).hexdigest()
        attempts = list(await db.scalars(select(LlmModelAttempt)))
        assert all(attempt.context_build_id for attempt in attempts)
        audits = [json.loads(storage.objects[a.raw_request_uri][0]) for a in attempts]
        assert any(
            any(m.get("tool_call_id") == "read-1" for m in a.get("effective_messages", []))
            for a in audits
        )


@pytest.mark.parametrize("same_owner", [True, False])
async def test_tool_cannot_read_other_conversation_or_unbound_file(client, same_owner):
    """A known private file ID is insufficient without the current conversation attachment grant."""
    from test_conversation_file_context import prepare_conversation_file

    owner, _, file, _ = await prepare_conversation_file(client)
    if same_owner:
        session = (await client.post("/api/v1/sessions", json={"user_id": owner})).json()
        other = (
            await client.post(
                f"/api/v1/sessions/{session['session_id']}/turns",
                json={"user_id": owner, "content_text": "Read another conversation"},
            )
        ).json()["run"]
    else:
        other = await create_test_run(client)
    provider = FileToolProvider(
        tool_name="read_file_content", arguments={"file_id": file["file_id"]}
    )
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)
    result = (
        await client.post(
            f"/api/v1/runs/{other['run_id']}/agent-workflows",
            json=workflow_payload(execution_profile="tool_enabled_v1"),
        )
    ).json()
    assert result["status"] == "completed", result
    last_tool = next(m for r in provider.requests for m in r.messages if m.role.value == "tool")
    assert json.loads(last_tool.content)["error_code"] == "file_unavailable"
    assert "UNIQUE_FILE_TEXT" not in last_tool.content


async def test_tool_progress_stream_is_committed_before_worker_completion(client):
    """A tool result produces ordered, replayable progress without exposing its raw content."""
    run = await create_test_run(client)
    provider = FileToolProvider()
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)
    response = await client.post(
        f"/api/v1/runs/{run['run_id']}/agent-workflows",
        json=workflow_payload(execution_profile="tool_enabled_v1", stream=True),
    )
    assert response.status_code == 201, response.text
    events = [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    assert [e["event_sequence"] for e in events] == list(range(1, len(events) + 1))
    progress = next(e for e in events if e["event_type"] == "agent.node.progress")
    completed = next(
        e
        for e in events
        if e["event_type"] == "agent.node.completed"
        and e["node_execution_id"] == progress["node_execution_id"]
    )
    assert progress["node_status"] == "running"
    assert progress["event_sequence"] < completed["event_sequence"]
    replay = await client.get(f"/api/v1/agent-workflows/{progress['workflow_execution_id']}/events")
    assert "event: agent.node.progress" in replay.text
    assert "output_json" not in json.dumps(progress)


async def test_deleted_file_is_not_resent_from_tool_history(
    client, test_database_session_factory, monkeypatch
):
    """Revocation between two model rounds prevents resending previously read content."""
    from test_conversation_file_context import prepare_conversation_file

    from nexuspilot_api.models import UserFile
    from nexuspilot_api.services.agent_file_tool_service import AgentFileToolService

    _, _, file, turn = await prepare_conversation_file(client)
    original = AgentFileToolService.execute

    async def delete_after_read(service, **kwargs):
        """Revoke the fixture after its result commit and before the next model invocation."""
        record = await original(service, **kwargs)
        stored_file = await service.db.get(UserFile, file["file_id"])
        stored_file.status = "deleted"
        await service.db.commit()
        return record

    monkeypatch.setattr(AgentFileToolService, "execute", delete_after_read)
    provider = FileToolProvider(
        tool_name="read_file_content", arguments={"file_id": file["file_id"]}
    )
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)
    result = (
        await client.post(
            f"/api/v1/runs/{turn['run']['run_id']}/agent-workflows",
            json=workflow_payload(execution_profile="tool_enabled_v1"),
        )
    ).json()
    assert result["status"] == "failed", result
    assert (
        len([r for r in provider.requests if r.metadata["agent_node_key"] == "worker_execution"])
        == 1
    )


async def test_duplicate_tool_call_is_not_executed_twice(client, test_database_session_factory):
    """A repeated logical call conflicts and leaves exactly one durable result."""
    from nexuspilot_api.core.errors import ResourceConflictError
    from nexuspilot_api.infrastructure.object_storage import get_object_storage
    from nexuspilot_api.services.agent_file_tool_service import AgentFileToolService

    run = await create_test_run(client)
    provider = FileToolProvider()
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)
    await client.post(
        f"/api/v1/runs/{run['run_id']}/agent-workflows",
        json=workflow_payload(execution_profile="tool_enabled_v1"),
    )
    async with test_database_session_factory() as db:
        record = await db.scalar(select(LlmModelToolCall))
        executor = AgentFileToolService(db, app.dependency_overrides[get_object_storage]())
        with pytest.raises(ResourceConflictError):
            await executor.execute(
                attempt_id=record.attempt_id,
                call=ToolCall(id="read-1", name="list_run_files", arguments={}),
            )
        assert len(list(await db.scalars(select(LlmModelToolCall)))) == 1


async def test_tool_timeout_is_persisted_and_returned_to_model(client, monkeypatch):
    """A bounded read failure becomes a structured result instead of a fake empty success."""
    from nexuspilot_api.services.agent_file_tool_service import AgentFileToolService

    async def timed_out_read(*args):
        """Inject storage timeout without a slow or network-dependent test."""
        raise TimeoutError()

    monkeypatch.setattr(AgentFileToolService, "_read", timed_out_read)
    run = await create_test_run(client)
    provider = FileToolProvider()
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)
    result = (
        await client.post(
            f"/api/v1/runs/{run['run_id']}/agent-workflows",
            json=workflow_payload(execution_profile="tool_enabled_v1"),
        )
    ).json()
    assert result["status"] == "completed", result
    worker = next(n for n in result["nodes"] if n["output_type"] == "agent_model_execution")
    assert worker["output"]["tool_results"][0]["status"] == "timed_out"
