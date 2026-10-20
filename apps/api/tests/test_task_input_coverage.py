"""Distinguish prepared model input from tool reads, provider receipt and understanding."""

import json

import pytest
from nexuspilot_models.contracts import Message, MessageRole, ModelResponse, ToolCall
from sqlalchemy import select
from test_agent_workflows import register_default_agent_test_model, scripted_registry  # noqa: F401
from test_conversation_file_context import prepare_conversation_file
from test_general_agent import GeneralProvider, general_model_catalog, general_payload  # noqa: F401
from test_model_input_manifest import delegated_message, runtime_sources, tool_message

from nexuspilot_api.core.dependencies import get_provider_registry
from nexuspilot_api.core.errors import ResourceConflictError, ResourceNotFoundError
from nexuspilot_api.features.agent_runtime.services.task_input_coverage import (
    authorize_input_coverage_history,
    summarize_input_request,
    task_input_coverage,
)
from nexuspilot_api.features.agent_runtime.services.task_note_tools import ListTaskNotesArguments
from nexuspilot_api.infrastructure.object_storage import get_object_storage
from nexuspilot_api.main import app
from nexuspilot_api.models import LlmTask, UserFile, new_id
from nexuspilot_api.services.model_input_manifest import (
    RuntimeSourceInput,
    build_delegated_source_manifest,
    build_runtime_source_manifest,
    build_tool_input_manifest,
)


def prepared_request(messages):
    """Build the exact persisted request shape without a provider or database."""
    return {
        "effective_messages": [m.model_dump(mode="json") for m in messages],
        "tool_input_manifest": build_tool_input_manifest(messages),
        "delegated_source_manifest": build_delegated_source_manifest(messages),
        "runtime_source_manifest": None,
    }


def test_input_summary_counts_occurrences_and_deduplicates_same_snapshot_blocks():
    """Duplicate delivery is not more coverage; child prose never becomes original input."""
    source = RuntimeSourceInput(runtime_sources(), (("original_sources",),))
    messages = [tool_message(), tool_message(), delegated_message(),
                Message(role=MessageRole.USER, content=source.serialized_input)]
    raw = prepared_request(messages)
    raw["runtime_source_manifest"] = build_runtime_source_manifest(messages, source)
    summary = summarize_input_request(raw)
    assert summary["audit_complete"] is True
    assert summary["block_occurrences"] == 4
    assert len(summary["prepared_blocks"]) == 2
    assert summary["model_understanding_verified"] is False
    assert summary["provider_receipt_verified"] is False
    assert "原文" not in json.dumps(summary, ensure_ascii=False)


def test_historical_missing_input_audits_are_unknown_not_zero():
    """Available old messages can be reconstructed without inventing saved-at-dispatch audits."""
    raw = prepared_request([tool_message()])
    del raw["delegated_source_manifest"]
    summary = summarize_input_request(raw)
    assert summary["audit_complete"] is False
    assert "delegated_source_manifest" in summary["missing_audits"]
    assert summary["block_occurrences"] == 1
    assert summarize_input_request({})["block_occurrences"] is None


def test_modified_prepared_body_fails_manifest_comparison():
    """Corrupt object contents must fail instead of becoming empty or trusted coverage."""
    raw = prepared_request([tool_message()])
    raw["effective_messages"][0]["content"] = "changed"
    with pytest.raises(ResourceConflictError):
        summarize_input_request(raw)


async def test_input_tool_uses_current_task_requests_and_rechecks_file_access(
    client, test_database_session_factory,
):
    """Exercise discovery, actual request objects, paging, task isolation and revoked history."""
    _, _, file, turn = await prepare_conversation_file(client)
    provider = GeneralProvider()
    summaries = []

    async def generate(request):
        """Read one original, then query prepared inputs without asking for another file read."""
        provider.requests.append(request)
        previous = [m for m in request.messages if m.role.value == "tool"]
        steps = [("describe_tools", {"name": "get_task_input_coverage", "version": "1.0.0"}),
                 ("read_file_content", {"file_id": file["file_id"], "limit": 1}),
                 ("get_task_input_coverage", {})]
        if len(previous) == len(steps):
            envelope = json.loads(previous[-1].content)
            assert envelope["status"] == "completed", envelope
            summaries.append(envelope["result"])
            return ModelResponse(text="The supplied file text was read [S1].", finish_reason="stop",
                                 input_tokens=10, output_tokens=5, latency_ms=1, raw_response={})
        name, arguments = steps[len(previous)]
        return ModelResponse(tool_calls=[ToolCall(id=f"input-{len(previous)}", name=name,
                                                  arguments=arguments)], finish_reason="tool_calls",
                             input_tokens=10, output_tokens=5, latency_ms=1, raw_response={})

    provider.generate = generate
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)
    response = await client.post(f"/api/v1/runs/{turn['run']['run_id']}/agent-workflows",
                                 json=general_payload("never"))
    assert response.json()["status"] == "completed", response.text
    result = summaries[0]
    assert len(result["attempts"]) == 3
    assert sum(a["block_occurrences"] for a in result["attempts"]) == 1
    assert all(a["audit_complete"] for a in result["attempts"])
    assert "UNIQUE_FILE_TEXT_2718" not in json.dumps(result)
    async with test_database_session_factory() as db:
        storage = app.dependency_overrides[get_object_storage]()
        task = await db.scalar(select(LlmTask).where(LlmTask.run_id == turn["run"]["run_id"]))
        first = await task_input_coverage(db, storage, run_id=task.run_id, task_id=task.task_id,
                                         arguments=ListTaskNotesArguments(limit=1))
        assert len(first["attempts"]) == 1 and first["next_offset"] == 1
        other = LlmTask(task_id=new_id(), run_id=task.run_id, task_type="general_agent",
                        title="Separate", objective="Separate inputs")
        db.add(other)
        await db.commit()
        isolated = await task_input_coverage(db, storage, run_id=task.run_id, task_id=other.task_id,
                                            arguments=ListTaskNotesArguments())
        assert isolated["attempts"] == []
        with pytest.raises(ResourceNotFoundError):
            await task_input_coverage(db, storage, run_id=new_id(), task_id=task.task_id,
                                      arguments=ListTaskNotesArguments())
        saved = await db.get(UserFile, file["file_id"])
        saved.status = "deleted"
        await db.commit()
        with pytest.raises(ResourceConflictError):
            await authorize_input_coverage_history(db, run_id=task.run_id, result=result)
