"""Verify task-local reading progress without mistaking search or notes for full reading."""

import json
from types import SimpleNamespace

import pytest
from nexuspilot_models.contracts import ModelResponse, ToolCall
from sqlalchemy import select, text
from test_agent_workflows import register_default_agent_test_model, scripted_registry  # noqa: F401
from test_api import create_test_run
from test_conversation_file_context import prepare_conversation_file
from test_general_agent import GeneralProvider, general_model_catalog, general_payload  # noqa: F401

from nexuspilot_api.core.dependencies import get_provider_registry
from nexuspilot_api.core.errors import ResourceConflictError, ResourceNotFoundError
from nexuspilot_api.features.agent_runtime.services.task_read_coverage import (
    summarize_task_reads,
    task_read_coverage,
)
from nexuspilot_api.infrastructure.object_storage import get_object_storage
from nexuspilot_api.main import app
from nexuspilot_api.models import LlmModelToolCall, LlmTask, UserFile, new_id
from nexuspilot_api.schemas.file_parsing import ParsedFileBlock
from nexuspilot_api.services.agent_file_tool_service import PageArguments


class CoverageProvider(GeneralProvider):
    """Inspect one attached file before and after an actual native read."""

    def __init__(self, file_id, *, repeat_without_read=False):
        """Retain the authorized file and observations across model rounds."""
        super().__init__()
        self.file_id = file_id
        self.coverage = []
        self.repeat_without_read = repeat_without_read

    async def generate(self, request):
        """Load the progress tool, query an unread file, read, then query again."""
        self.requests.append(request)
        previous = [message for message in request.messages if message.role.value == "tool"]
        if previous and previous[-1].tool_name == "get_task_read_coverage":
            result = json.loads(previous[-1].content)
            assert result["status"] == "completed", result
            self.coverage.append(result["result"])
        steps = [
            ("describe_tools", {"name": "get_task_read_coverage", "version": "1.0.0"}),
            ("get_task_read_coverage", {"file_id": self.file_id}),
            ("read_file_content", {"file_id": self.file_id, "limit": 1}),
            ("get_task_read_coverage", {"file_id": self.file_id}),
        ]
        if self.repeat_without_read:
            steps[2] = steps[1]
        if len(previous) == len(steps):
            return ModelResponse(text="Read the available text. [S1]", finish_reason="stop",
                                 input_tokens=10, output_tokens=5, latency_ms=1, raw_response={})
        name, arguments = steps[len(previous)]
        return ModelResponse(tool_calls=[ToolCall(id=f"coverage-{len(previous)}", name=name,
                                                  arguments=arguments)],
                             finish_reason="tool_calls", input_tokens=10, output_tokens=5,
                             latency_ms=1, raw_response={})


async def test_task_read_coverage_counts_only_actual_reads_and_survives_restore(
    client, test_database_session_factory,
):
    """An untouched attachment starts at zero; metadata checks do not consume source blocks."""
    _, _, file, turn = await prepare_conversation_file(client)
    provider = CoverageProvider(file["file_id"])
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)
    response = await client.post(f"/api/v1/runs/{turn['run']['run_id']}/agent-workflows",
                                 json=general_payload("never"))
    assert response.status_code == 201, response.text
    assert response.json()["status"] == "completed", response.text
    before, after = provider.coverage
    assert before["returned_block_count"] == 0
    assert before["unreturned_block_count"] == before["total_parsed_blocks"] == 1
    assert before["next_unreturned_blocks"][0]["read_arguments"] == {
        "file_id": file["file_id"], "block_id": "line:1", "before": 0, "limit": 20,
    }
    assert "UNIQUE_FILE_TEXT_2718" not in json.dumps(before)
    assert after["returned_block_count"] == after["total_parsed_blocks"] == 1
    assert after["unreturned_block_count"] == 0
    assert after["all_parsed_blocks_returned"] is True
    assert after["next_unreturned_blocks"] == []
    assert after["model_understanding_verified"] is False
    assert after["scope"] == "current_task_tool_results"
    restored = await client.get(
        f"/api/v1/agent-workflows/{response.json()['workflow_execution_id']}/result"
    )
    assert restored.status_code == 200
    assert restored.json()["final_output"] == response.json()["final_output"]
    async with test_database_session_factory() as db:
        storage = app.dependency_overrides[get_object_storage]()
        saved = list(await db.scalars(select(LlmModelToolCall).where(
            LlmModelToolCall.tool_name == "get_task_read_coverage",
        )))
        assert len(saved) == 2
        for record in saved:
            raw = (await db.execute(text(
                "SELECT output_json, body_ref_json FROM llm_tool_calls WHERE tool_call_id=:id"
            ), {"id": record.tool_call_id})).one()
            assert raw.output_json in (None, "null", "{}")
            assert raw.body_ref_json
        task = await db.scalar(select(LlmTask).where(LlmTask.run_id == turn["run"]["run_id"]))
        arguments = PageArguments(file_id=file["file_id"])
        # An independent task in the same Run has not read the parent's material.
        independent = LlmTask(task_id=new_id(), run_id=task.run_id, task_type="general_agent",
                              title="Independent reader", objective="Read independently")
        db.add(independent)
        await db.commit()
        other = await task_read_coverage(
            db, storage, run_id=task.run_id,
            task_id=independent.task_id, arguments=arguments,
        )
        assert other["returned_block_count"] == 0
        unrelated = await create_test_run(client)
        with pytest.raises(ResourceNotFoundError):
            await task_read_coverage(db, storage, run_id=unrelated["run_id"],
                                     task_id=independent.task_id, arguments=arguments)
        source = await db.get(UserFile, file["file_id"])
        source.status = "deleted"
        await db.commit()
        with pytest.raises(ResourceNotFoundError):
            await task_read_coverage(db, storage, run_id=task.run_id,
                                     task_id=task.task_id, arguments=arguments)


async def test_coverage_poll_without_new_file_content_still_stops(client):
    """Do not turn state-aware progress queries into an unlimited polling loophole."""
    _, _, file, turn = await prepare_conversation_file(client)
    provider = CoverageProvider(file["file_id"], repeat_without_read=True)
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)
    response = await client.post(f"/api/v1/runs/{turn['run']['run_id']}/agent-workflows",
                                 json=general_payload("never"))
    assert response.status_code == 201
    result = response.json()
    assert result["status"] == "failed"
    assert "without progress" in result["error"]["public_message"]
    assert len(provider.requests) == 3
    assert len(provider.coverage) == 1


async def test_revoked_coverage_history_stops_before_next_model_request(client, monkeypatch):
    """Coverage metadata is also private file history and cannot survive a revoked source."""
    from nexuspilot_api.services.agent_file_tool_service import AgentFileToolService

    _, _, file, turn = await prepare_conversation_file(client)
    finish = AgentFileToolService._finish

    async def finish_and_revoke(service, record, status, **kwargs):
        """Revoke after persisting the first progress result, before the next model round."""
        result = await finish(service, record, status, **kwargs)
        if record.tool_name == "get_task_read_coverage" and status == "completed":
            source = await service.db.get(UserFile, file["file_id"])
            source.status = "deleted"
            await service.db.commit()
        return result

    monkeypatch.setattr(AgentFileToolService, "_finish", finish_and_revoke)
    provider = CoverageProvider(file["file_id"])
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)
    response = await client.post(f"/api/v1/runs/{turn['run']['run_id']}/agent-workflows",
                                 json=general_payload("never"))
    assert response.status_code == 201
    assert response.json()["status"] == "failed"
    assert len(provider.requests) == 2


def coverage_fixture():
    """Represent a partially extracted three-block document with two unread blocks."""
    file = SimpleNamespace(file_id="file", content_hash="source", parsed_hash="parsed")
    blocks = [ParsedFileBlock(block_id=f"line:{i}", text=f"original {i}") for i in range(3)]
    document = SimpleNamespace(blocks=blocks, parser_version="parser", status="partial",
                               warnings=["No OCR"], uncovered_pages=[2])
    result = {"file_id": "file", "source_hash": "source", "parsed_hash": "parsed",
              "parser_version": "parser", "data": {"blocks": [blocks[1].model_dump(mode="json")]},
              "read_scope": {"total_blocks": 300, "query": "original"}}
    return document, file, result


def test_search_scope_and_duplicate_reads_do_not_inflate_coverage():
    """Count exact returned blocks once and page locations even when search scanned everything."""
    document, file, result = coverage_fixture()
    report = summarize_task_reads(document, file, [result, result], offset=0, limit=1)
    assert report["returned_block_count"] == 1
    assert report["unreturned_block_count"] == 2
    assert report["all_parsed_blocks_returned"] is False
    assert report["next_offset"] == 1
    assert report["next_unreturned_blocks"][0]["source_offset"] == 0
    second = summarize_task_reads(document, file, [result], offset=1, limit=1)
    assert second["next_unreturned_blocks"][0]["source_offset"] == 2
    assert second["next_offset"] is None
    assert second["warnings"] == ["No OCR"]
    assert second["uncovered_pages"] == [2]
    document.blocks = []
    empty = summarize_task_reads(document, file, [], offset=0, limit=1)
    assert empty["all_parsed_blocks_returned"] is False


@pytest.mark.parametrize("changed", ["snapshot", "text", "unknown_id"])
def test_progress_rejects_changed_snapshots_or_fabricated_original_blocks(changed):
    """Invalid stored content must fail rather than masquerade as unread or verified material."""
    document, file, result = coverage_fixture()
    if changed == "snapshot":
        result["parsed_hash"] = "other"
    else:
        result["data"]["blocks"][0]["text" if changed == "text" else "block_id"] = "forged"
    with pytest.raises(ResourceConflictError):
        summarize_task_reads(document, file, [result], offset=0, limit=1)
