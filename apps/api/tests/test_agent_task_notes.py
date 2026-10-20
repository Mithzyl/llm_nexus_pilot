"""Exercise private task notes through the general loop and existing object persistence."""

import json

import pytest
from nexuspilot_models.contracts import ModelResponse, ToolCall
from sqlalchemy import select, text
from test_agent_workflows import (
    register_default_agent_test_model,  # noqa: F401
    scripted_registry,
)
from test_api import create_test_run
from test_general_agent import (
    GeneralProvider,
    general_model_catalog,  # noqa: F401
    general_payload,
)

from nexuspilot_api.core.dependencies import get_provider_registry
from nexuspilot_api.main import app
from nexuspilot_api.models import LlmModelToolCall


class NoteProvider(GeneralProvider):
    """Save a task note, read its committed ID, then produce an ordinary answer."""

    def __init__(self, arguments=None, foreign_note_id=None, file_id=None, list_notes=False):
        """Choose a normal save or a deliberately unauthorized read for this fixture."""
        super().__init__()
        self.arguments = arguments or {
            "note": "Tentative progress, not a verified fact",
            "unresolved_items": ["Check the other chapter"],
            "next_actions": ["Search the specific term"], "source_aliases": [],
        }
        self.foreign_note_id = foreign_note_id
        self.file_id = file_id
        self.list_notes = list_notes
        self.note_id = None
        self.results = []

    async def generate(self, request):
        """Use the advertised note tools and assert saved material survives the next call."""
        self.requests.append(request)
        previous = [m for m in request.messages if m.role.value == "tool"]
        assert "save_task_note" in {tool.name for tool in request.tools}
        if not previous:
            name = "read_task_note" if self.foreign_note_id else "save_task_note"
            arguments = (
                {"note_id": self.foreign_note_id} if self.foreign_note_id else self.arguments
            )
            if self.file_id:
                name, arguments = "read_file_content", {"file_id": self.file_id, "limit": 1}
        else:
            result = json.loads(previous[-1].content)
            self.results.append(result)
            if result["status"] == "completed" and previous[-1].tool_name == "read_file_content":
                name, arguments = "save_task_note", {**self.arguments, "source_aliases": ["S1"]}
            elif result["status"] == "completed" and previous[-1].tool_name == "save_task_note":
                self.note_id = result["result"]["note_id"]
                name, arguments = "read_task_note", {"note_id": self.note_id}
            elif self.list_notes and previous[-1].tool_name == "read_task_note":
                name, arguments = "describe_tools", {"name": "list_task_notes", "version": "1.0.0"}
            elif self.list_notes and previous[-1].tool_name == "describe_tools":
                name, arguments = "list_task_notes", {"limit": 1}
            else:
                return ModelResponse(text="Finished with the recorded limitations."
                                     + (" [S1]" if self.file_id else ""),
                                     finish_reason="stop", input_tokens=10, output_tokens=5,
                                     latency_ms=1, raw_response={"fixture": "note-answer"})
        return ModelResponse(tool_calls=[ToolCall(id=f"note-{len(previous)}", name=name,
                                                  arguments=arguments)],
                             finish_reason="tool_calls", input_tokens=10, output_tokens=5,
                             latency_ms=1, raw_response={"fixture": "note-tool"})


async def run_notes(client, provider):
    """Create one isolated test Run and execute the explicit general workflow."""
    run = await create_test_run(client)
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)
    response = await client.post(f"/api/v1/runs/{run['run_id']}/agent-workflows",
                                 json=general_payload("never"))
    assert response.status_code == 201, response.text
    assert response.json()["status"] == "completed", response.text
    return run


async def test_task_note_is_saved_and_read_from_minio_backed_tool_result(
    client, test_database_session_factory,
):
    """Persist model-authored notes as immutable private tool output, not SQL body or facts."""
    provider = NoteProvider()
    await run_notes(client, provider)
    saved, restored = provider.results
    assert saved["result"] == restored["result"]
    assert saved["result"]["content_verified"] is False
    assert saved["result"]["note"] == provider.arguments["note"]
    async with test_database_session_factory() as db:
        record = await db.get(LlmModelToolCall, provider.note_id)
        assert record.risk_level == "task_write"
        assert record.output_json["result"] == saved["result"]
        raw = (await db.execute(text(
            "SELECT input_json, output_json, body_ref_json FROM llm_tool_calls "
            "WHERE tool_call_id=:id"
        ), {"id": provider.note_id})).one()
        assert raw.input_json in (None, "null", "{}")
        assert raw.output_json in (None, "null", "{}")
        assert raw.body_ref_json


async def test_other_task_cannot_read_a_known_note_id(client):
    """A valid ID from another Run gives the same denial as an unknown note."""
    first = NoteProvider()
    await run_notes(client, first)
    for note_id in (first.note_id, "00000000-0000-0000-0000-000000000000"):
        second = NoteProvider(foreign_note_id=note_id)
        await run_notes(client, second)
        assert second.results[-1]["status"] == "permission_denied"
        assert second.results[-1]["error_code"] == "note_unavailable"
        assert second.results[-1]["result"] is None


@pytest.mark.parametrize("arguments,error", [
    ({"note": "Guess", "source_aliases": ["S999"]}, "unknown_note_source"),
    ({"note": "Guess", "user_id": "forged"}, "invalid_arguments"),
])
async def test_invalid_note_arguments_never_create_a_successful_note(
    client, test_database_session_factory, arguments, error,
):
    """Reject fabricated sources and caller-supplied identity instead of persisting false facts."""
    provider = NoteProvider(arguments=arguments)
    await run_notes(client, provider)
    assert provider.results[-1]["status"] == "failed"
    assert provider.results[-1]["error_code"] == error
    async with test_database_session_factory() as db:
        saved = list(await db.scalars(select(LlmModelToolCall).where(
            LlmModelToolCall.tool_name == "save_task_note", LlmModelToolCall.status == "completed",
        )))
        assert not saved


async def test_invalid_note_feedback_identifies_field_without_echoing_supplied_values(client):
    """Give the model actionable schema feedback while withholding arbitrary input and key names."""
    provider = NoteProvider(arguments={
        "note": "draft",
        "source_aliases": ["PRIVATE_SOURCE"],
        "PRIVATE_FIELD": "PRIVATE_VALUE",
    })
    await run_notes(client, provider)
    result = provider.results[-1]
    assert result["status"] == "failed"
    assert result["error_code"] == "invalid_arguments"
    assert {"field": "source_aliases", "index": 0, "type": "string_pattern_mismatch"} in (
        result["result"]["validation_errors"]
    )
    assert {"field": None, "type": "extra_forbidden"} in result["result"]["validation_errors"]
    assert "PRIVATE_" not in json.dumps(result)


async def test_note_can_correct_arguments_after_failed_tool_without_weakening_schema(
    client, test_database_session_factory,
):
    """Keep the failed call, allow a separately valid call, and read only the committed note."""
    class CorrectingNoteProvider(NoteProvider):
        """Fix one malformed source-array entry after inspecting the failed tool result."""

        async def generate(self, request):
            """Use returned field feedback instead of repeating the identical invalid call."""
            previous = [message for message in request.messages if message.role.value == "tool"]
            if len(previous) == 1:
                feedback = json.loads(previous[-1].content)
                assert feedback["status"] == "failed"
                assert feedback["result"]["validation_errors"][0]["field"] == "source_aliases"
                self.requests.append(request)
                return ModelResponse(
                    tool_calls=[ToolCall(
                        id="corrected-note", name="save_task_note",
                        arguments={"note": "No source has been read yet.", "source_aliases": []},
                    )],
                    finish_reason="tool_calls", input_tokens=10, output_tokens=5,
                    latency_ms=1, raw_response={"fixture": "corrected-note"},
                )
            return await super().generate(request)

    provider = CorrectingNoteProvider(arguments={"note": "draft", "source_aliases": ["[S1]"]})
    await run_notes(client, provider)
    assert provider.note_id is not None
    async with test_database_session_factory() as db:
        calls = list(await db.scalars(select(LlmModelToolCall).where(
            LlmModelToolCall.tool_name == "save_task_note",
        )))
        assert sorted(call.status for call in calls) == ["completed", "failed"]
        failed = next(call for call in calls if call.status == "failed")
        assert failed.error_code == "invalid_arguments"


@pytest.mark.parametrize("revoke", [False, True])
async def test_note_keeps_actual_sources_and_history_rechecks_revoked_files(
    client, test_database_session_factory, monkeypatch, revoke,
):
    """A note retains real file snapshots; revoked originals stop subsequent model input."""
    from test_conversation_file_context import prepare_conversation_file

    from nexuspilot_api.models import UserFile
    from nexuspilot_api.services.agent_file_tool_service import AgentFileToolService

    _, _, file, turn = await prepare_conversation_file(client)
    original_finish = AgentFileToolService._finish

    async def finish_and_revoke(service, record, status, **kwargs):
        """Revoke the attachment after a committed note, before the next model call."""
        result = await original_finish(service, record, status, **kwargs)
        if revoke and record.tool_name == "save_task_note" and status == "completed":
            source = await service.db.get(UserFile, file["file_id"])
            source.status = "deleted"
            await service.db.commit()
        return result

    monkeypatch.setattr(AgentFileToolService, "_finish", finish_and_revoke)
    provider = NoteProvider(file_id=file["file_id"])
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)
    response = await client.post(f"/api/v1/runs/{turn['run']['run_id']}/agent-workflows",
                                 json=general_payload("never"))
    result = response.json()
    assert result["status"] == ("failed" if revoke else "completed"), result
    if revoke:
        assert len(provider.requests) == 2
    async with test_database_session_factory() as db:
        note = await db.scalar(select(LlmModelToolCall).where(
            LlmModelToolCall.tool_name == "save_task_note",
        ))
        reference = note.output_json["result"]["sources"]["S1"]
        read = await db.get(LlmModelToolCall, reference["tool_call_id"])
        assert reference == read.output_json["result"]["evidence_ref"]
        assert reference["file_id"] == file["file_id"]
        assert note.output_json["result"]["content_verified"] is False


async def test_note_catalog_loads_listing_without_enabling_it_for_legacy_tools(client):
    """Discover the note list by fixed version, keeping the legacy file catalog unchanged."""
    from nexuspilot_api.services.agent_file_tool_service import FILE_TOOL_CATALOG

    provider = NoteProvider(list_notes=True)
    await run_notes(client, provider)
    result = provider.results[-1]
    assert result["status"] == "completed"
    assert [item["note_id"] for item in result["result"]["notes"]] == [provider.note_id]
    assert result["result"]["next_offset"] is None
    assert not any("task_note" in entry.name for entry in FILE_TOOL_CATALOG.entries)
    assert "list_task_notes" not in {tool.name for tool in provider.requests[0].tools}
    assert "list_task_notes" in {tool.name for tool in provider.requests[-1].tools}


@pytest.mark.parametrize("task_status", ["completed", "cancel_requested"])
async def test_terminal_or_stopping_task_cannot_write_another_note(
    client, test_database_session_factory, task_status,
):
    """Direct dispatch also rejects late writes after completion or an explicit stop request."""
    from nexuspilot_api.features.agent_runtime.services.task_note_tools import (
        GeneralAgentToolService,
    )
    from nexuspilot_api.infrastructure.object_storage import get_object_storage
    from nexuspilot_api.models import LlmModelAttempt, LlmTask, TaskStatus

    provider = NoteProvider()
    await run_notes(client, provider)
    storage = app.dependency_overrides[get_object_storage]()
    async with test_database_session_factory() as db:
        saved = await db.get(LlmModelToolCall, provider.note_id)
        attempt = await db.get(LlmModelAttempt, saved.attempt_id)
        task = await db.get(LlmTask, attempt.task_id)
        task.status = TaskStatus(task_status)
        await db.commit()
        tools = GeneralAgentToolService(db, storage, task_id=task.task_id,
                                       sources={}, allowed_task_ids={task.task_id})
        result = await tools.execute(attempt_id=attempt.attempt_id, call=ToolCall(
            id="late-write", name="save_task_note", arguments={"note": "Must not save"},
        ))
        assert result.status == "permission_denied"
        assert result.error_code == "task_not_running"
        assert result.output_json["result"] is None
        assert (await db.get(LlmModelToolCall, provider.note_id)).status == "completed"
