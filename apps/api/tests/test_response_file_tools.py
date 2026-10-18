"""Specify automatic file tools in quick replies with durable multi-round SSE and ownership."""

import asyncio
import json

import pytest
from nexuspilot_models.contracts import (
    ModelResponse,
    ProviderContinuationKind,
    ProviderContinuationState,
    StreamEvent,
    StreamEventType,
    ToolCall,
)
from sqlalchemy import select
from test_agent_workflows import ScriptedAgentProvider, scripted_registry
from test_conversation_file_context import prepare_conversation_file

from nexuspilot_api.core.dependencies import get_provider_registry
from nexuspilot_api.main import app
from nexuspilot_api.models import LlmModelAttempt, LlmModelToolCall


class QuickFileProvider(ScriptedAgentProvider):
    """List attachments, read the discovered ID, then stream an answer from actual tool data."""

    async def generate(self, request):
        """Exercise native tool continuation without a Controller or paid model."""
        self.requests.append(request)
        if request.tools:
            assert "list_run_files" in {tool.name for tool in request.tools}
        results = [message for message in request.messages if message.role.value == "tool"]
        for message in request.messages:
            if message.role.value == "assistant" and message.tool_calls:
                assert message._provider_continuation_state is not None
                assert message._provider_continuation_state.encrypted_reasoning_items == [
                    {"type": "reasoning", "encrypted_content": "PRIVATE_TEST_CONTINUATION"}
                ]
        if not results:
            assert all("UNIQUE_FILE_TEXT_2718" not in m.content for m in request.messages)
            calls = [ToolCall(id="list-1", name="list_run_files", arguments={})]
        elif len(results) == 1:
            file_id = json.loads(results[0].content)["result"]["files"][0]["file_id"]
            calls = [
                ToolCall(id="read-2", name="read_file_content", arguments={"file_id": file_id})
            ]
        else:
            assert "UNIQUE_FILE_TEXT_2718" in results[-1].content
            return ModelResponse(
                text="File answer: UNIQUE_FILE_TEXT_2718",
                finish_reason="stop",
                input_tokens=30,
                output_tokens=10,
                latency_ms=1,
                raw_response={"fixture": "quick-answer"},
            )
        return ModelResponse(
            tool_calls=calls,
            finish_reason="tool_calls",
            input_tokens=30,
            output_tokens=10,
            latency_ms=1,
            raw_response={"fixture": "quick-tool"},
            provider_continuation_state=ProviderContinuationState(
                kind=ProviderContinuationKind.OPENAI_ENCRYPTED_ITEMS,
                encrypted_reasoning_items=[
                    {"type": "reasoning", "encrypted_content": "PRIVATE_TEST_CONTINUATION"}
                ],
            ),
        )

    async def stream(self, request):
        """Send real normalized incremental events for each native round."""
        yield StreamEvent(type=StreamEventType.STARTED, sequence=1)
        response = await self.generate(request)
        if response.text:
            yield StreamEvent(
                type=StreamEventType.TEXT_DELTA, sequence=2, data={"delta": response.text}
            )
        yield StreamEvent(
            type=StreamEventType.COMPLETED,
            sequence=3,
            data={"response": response.model_dump(mode="json")},
            private_data={
                "provider_continuation_state": response.provider_continuation_state.model_dump(
                    mode="json"
                )
            }
            if response.provider_continuation_state
            else {},
        )


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("followup", [False, True])
@pytest.mark.parametrize("timeout_seconds", [60, None])
async def test_quick_reply_automatically_reads_files_and_replays_one_final_answer(
    client,
    test_database_session_factory,
    stream,
    followup,
    timeout_seconds,
):
    """Both HTTP modes retain tool rounds and only finish on the final answer."""
    owner, session, _, turn = await prepare_conversation_file(client)
    if followup:
        turn = (
            await client.post(
                f"/api/v1/sessions/{session['session_id']}/turns",
                json={"user_id": owner, "content_text": "Read the previous attachment"},
            )
        ).json()
    provider = QuickFileProvider()
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)
    reply = await client.post(
        "/api/v1/responses",
        json={
            "run_id": turn["run"]["run_id"],
            "current_user_message_id": turn["message"]["message_id"],
            "provider": "openai",
            "model": "test-model",
            "input": "Read the attachment",
            "timeout_seconds": timeout_seconds,
            "stream": stream,
            "idempotency_key": "quick-file-tools-test",
        },
    )
    assert reply.status_code == 200, reply.text
    assert "PRIVATE_TEST_CONTINUATION" not in reply.text
    if stream:
        events = [
            json.loads(line[6:]) for line in reply.text.splitlines() if line.startswith("data: ")
        ]
        assert [event["sequence"] for event in events] == list(range(1, len(events) + 1))
        assert len([event for event in events if event["type"] == "response.completed"]) == 1
        result = events[-1]["data"]["response"]
        assert any(event["type"] == "response.text.delta" for event in events)
        root_id = events[0]["response_id"]
        replay = await client.get(f"/api/v1/attempts/{root_id}/events")
        assert replay.status_code == 200, replay.text
        assert replay.json()["items"][-1]["data"]["response"] == result
    else:
        result = reply.json()
    assert result["output_text"] == "File answer: UNIQUE_FILE_TEXT_2718"
    assert result["tool_calls"] == []
    assert result["usage"]["input_tokens"] == 90
    assert len(provider.requests) == 3
    detail = (await client.get(f"/api/v1/runs/{turn['run']['run_id']}")).json()
    assert detail["status"] == "completed"
    async with test_database_session_factory() as db:
        attempts = list(await db.scalars(select(LlmModelAttempt)))
        calls = list(await db.scalars(select(LlmModelToolCall)))
        assert len(attempts) == 3 and all(a.status.value == "completed" for a in attempts)
        assert all(a.request_type == "file_tool_round" for a in attempts)
        assert len(calls) == 2 and all(c.status == "completed" for c in calls)
        assert any("UNIQUE_FILE_TEXT_2718" in str(c.output_json) for c in calls)


async def test_quick_tools_keep_tools_available_when_model_decides_to_answer(client):
    """Useful evidence can produce a final answer without forcibly withdrawing tools."""
    _, _, _, turn = await prepare_conversation_file(client)
    provider = QuickFileProvider()
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)
    reply = await client.post("/api/v1/responses", json={
        "run_id": turn["run"]["run_id"],
        "current_user_message_id": turn["message"]["message_id"],
        "provider": "openai", "model": "test-model", "input": "Read attachment",
    })
    assert reply.status_code == 200, reply.text
    assert reply.json()["output_text"] == "File answer: UNIQUE_FILE_TEXT_2718"
    assert provider.requests[-1].tools


@pytest.mark.parametrize("behavior", ["answer", "repeat", "forbidden"])
async def test_automatic_quick_tools_allow_direct_answer_and_bound_unsafe_calls(
    client,
    test_database_session_factory,
    behavior,
):
    """Tools are optional, repeated calls stop, and commands never acquire execution permission."""
    _, _, _, turn = await prepare_conversation_file(client)

    class PolicyProvider(QuickFileProvider):
        """Choose a direct answer, repeated read or disallowed command deterministically."""

        async def generate(self, request):
            """Observe the server's offered tools and consume denial without claiming a read."""
            self.requests.append(request)
            assert "read_file_content" in {tool.name for tool in request.tools}
            previous = [m for m in request.messages if m.role.value == "tool"]
            if behavior == "answer" or (behavior == "forbidden" and previous):
                if previous:
                    assert json.loads(previous[-1].content)["status"] == "permission_denied"
                return ModelResponse(
                    text="No file read needed", finish_reason="stop", latency_ms=1, raw_response={}
                )
            return ModelResponse(
                tool_calls=[
                    ToolCall(
                        id=f"call-{len(previous)}",
                        name="run_command" if behavior == "forbidden" else "list_run_files",
                        arguments={},
                    )
                ],
                finish_reason="tool_calls",
                latency_ms=1,
                raw_response={},
            )

    provider = PolicyProvider()
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)
    reply = await client.post(
        "/api/v1/responses",
        json={
            "run_id": turn["run"]["run_id"],
            "current_user_message_id": turn["message"]["message_id"],
            "provider": "openai",
            "model": "test-model",
            "input": "Use judgment",
            "stream": True,
        },
    )
    events = [json.loads(line[6:]) for line in reply.text.splitlines() if line.startswith("data: ")]
    assert events[-1]["type"] == (
        "response.failed" if behavior == "repeat" else "response.completed"
    )
    assert len(provider.requests) == (1 if behavior == "answer" else 2)
    async with test_database_session_factory() as db:
        calls = list(await db.scalars(select(LlmModelToolCall)))
        assert len(calls) == (0 if behavior == "answer" else 1)
        if behavior == "forbidden":
            assert calls[0].status == "permission_denied"


async def test_quick_file_history_is_not_resent_after_deletion(client, monkeypatch):
    """Recheck file permission between rounds so saved tool text cannot bypass deletion."""
    from nexuspilot_api.models import UserFile
    from nexuspilot_api.services.agent_file_tool_service import AgentFileToolService

    _, _, file, turn = await prepare_conversation_file(client)
    original = AgentFileToolService.execute

    async def delete_after_read(service, **kwargs):
        """Revoke the bound file immediately after a successful actual read."""
        record = await original(service, **kwargs)
        if kwargs["call"].name == "read_file_content":
            stored = await service.db.get(UserFile, file["file_id"])
            stored.status = "deleted"
            await service.db.commit()
        return record

    monkeypatch.setattr(AgentFileToolService, "execute", delete_after_read)
    provider = QuickFileProvider()
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)
    reply = await client.post(
        "/api/v1/responses",
        json={
            "run_id": turn["run"]["run_id"],
            "current_user_message_id": turn["message"]["message_id"],
            "provider": "openai",
            "model": "test-model",
            "input": "Read",
            "stream": True,
        },
    )
    events = [json.loads(line[6:]) for line in reply.text.splitlines() if line.startswith("data: ")]
    assert events[-1]["type"] == "response.failed"
    assert len(provider.requests) == 2


@pytest.mark.parametrize("timeout_seconds", [60, None])
@pytest.mark.parametrize("stream", [False, True])
async def test_cancelling_quick_tool_execution_does_not_launch_another_model_round(
    client, monkeypatch, timeout_seconds, stream
):
    """Propagate cancellation during a tool read without continuing to a paid model round."""
    from nexuspilot_api.services.agent_file_tool_service import AgentFileToolService

    _, _, _, turn = await prepare_conversation_file(client)
    reading = asyncio.Event()
    blocked = asyncio.Event()

    async def wait_during_read(service, **kwargs):
        """Represent an unfinished tool I/O operation until the request is cancelled."""
        from nexuspilot_api.models import LlmRun

        run = await service.db.get(LlmRun, turn["run"]["run_id"])
        assert run.status.value == "running"
        reading.set()
        await blocked.wait()

    monkeypatch.setattr(AgentFileToolService, "execute", wait_during_read)
    provider = QuickFileProvider()
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)
    request = asyncio.create_task(
        client.post(
            "/api/v1/responses",
            json={
                "run_id": turn["run"]["run_id"],
                "current_user_message_id": turn["message"]["message_id"],
                "provider": "openai",
                "model": "test-model",
                "input": "Read",
                "timeout_seconds": timeout_seconds,
                "stream": stream,
            },
        )
    )
    try:
        await asyncio.wait_for(reading.wait(), timeout=3)
    finally:
        request.cancel()
        with pytest.raises(asyncio.CancelledError):
            await request
    assert len(provider.requests) == 1
    detail = (await client.get(f"/api/v1/runs/{turn['run']['run_id']}")).json()
    assert detail["status"] == "cancelled"
