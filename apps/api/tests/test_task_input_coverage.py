"""Distinguish prepared model input from tool reads, provider receipt and understanding."""

import json
from datetime import datetime

import pytest
from nexuspilot_models.contracts import Message, MessageRole, ModelResponse, ToolCall
from sqlalchemy import select
from test_agent_workflows import register_default_agent_test_model, scripted_registry  # noqa: F401
from test_conversation_file_context import prepare_conversation_file
from test_general_agent import (  # noqa: F401
    GeneralProvider,
    general_model_catalog,
    general_payload,
    unit_review_text,
)
from test_model_input_manifest import delegated_message, runtime_sources, tool_message

from nexuspilot_api.core.dependencies import get_provider_registry
from nexuspilot_api.core.errors import ResourceConflictError, ResourceNotFoundError
from nexuspilot_api.features.agent_runtime.services.task_input_coverage import (
    authorize_input_coverage_history,
    summarize_input_request,
    task_input_coverage,
)
from nexuspilot_api.features.agent_runtime.services.task_input_snapshot import (
    read_task_input_snapshot,
    save_task_input_snapshot,
)
from nexuspilot_api.features.agent_runtime.services.task_note_tools import ListTaskNotesArguments
from nexuspilot_api.infrastructure.object_storage import get_object_storage
from nexuspilot_api.main import app
from nexuspilot_api.models import (
    AttemptStatus,
    LlmMemorySnapshotObject,
    LlmModelAttempt,
    LlmTask,
    User,
    UserFile,
    new_id,
)
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
    """Verify dated query pages remain historical facts after later requests and recheck access."""
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
    assert result["request_list_observed_at"]
    observed = datetime.fromisoformat(result["request_list_observed_at"])
    assert observed.utcoffset().total_seconds() == 0
    assert result["observation_kind"] == "live_query_page"
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
        # A later call exists now; the earlier recorded tool page is still correct.
        current = await task_input_coverage(db, storage, run_id=task.run_id, task_id=task.task_id,
                                           arguments=ListTaskNotesArguments(limit=20))
        assert len(current["attempts"]) == 4
        assert len(result["attempts"]) == 3
        assert current["request_list_observed_at"] >= result["request_list_observed_at"]
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


@pytest.mark.parametrize(("tries_own_task", "reads_pages"), [
    (False, True), (True, True), (False, False),
])
async def test_reviewer_reads_candidate_fixed_inputs_not_its_own_requests(
    client, test_database_session_factory, tries_own_task, reads_pages,
):
    """Fixed input facts are available immediately and optional page reads keep the same scope."""
    _, _, file, turn = await prepare_conversation_file(client)
    provider = GeneralProvider()
    pages = []
    references = []

    async def generate(request):
        """Verify the injected first page, then read fixed pages while Reviewer calls grow."""
        provider.requests.append(request)
        previous = [m for m in request.messages if m.role.value == "tool"]
        if request.metadata["agent_role"] == "controller":
            if not previous:
                return ModelResponse(
                    tool_calls=[ToolCall(id="main-read", name="read_file_content",
                                         arguments={"file_id": file["file_id"], "limit": 1})],
                    finish_reason="tool_calls", latency_ms=1, raw_response={},
                )
            return ModelResponse(
                text="The first request had no file blocks; the later request has one [S1].",
                finish_reason="stop", latency_ms=1, raw_response={},
            )
        contexts = []
        for message in request.messages:
            try:
                value = json.loads(message.content or "")
            except ValueError:
                continue
            if isinstance(value, dict) and "review_units" in value:
                contexts.append(value)
        assert contexts
        reference = contexts[-1].get("candidate_input_snapshot")
        assert reference is not None, "Reviewer needs the candidate's fixed input reference"
        evidence = contexts[-1].get("candidate_input_evidence")
        assert evidence is not None, "Reviewer needs an authorized first page, not only an ID"
        assert evidence["snapshot_id"] == reference["snapshot_id"]
        assert evidence["task_id"] == reference["task_id"]
        assert evidence["attempt_count"] == 2
        assert [a["block_occurrences"] for a in evidence["attempts"]] == [0, 1]
        assert evidence["next_offset"] is None
        references.append(reference)
        if not reads_pages:
            return ModelResponse(
                text=unit_review_text(request, "supported", "Checked the provided fixed page."),
                finish_reason="stop", latency_ms=1, raw_response={},
            )
        if tries_own_task and len(previous) < 2:
            name, arguments = (
                ("describe_tools", {"name": "get_task_input_coverage", "version": "1.0.0"})
                if not previous else ("get_task_input_coverage", {})
            )
            return ModelResponse(
                tool_calls=[ToolCall(id=f"wrong-scope-{len(previous)}", name=name,
                                     arguments=arguments)],
                finish_reason="tool_calls", latency_ms=1, raw_response={},
            )
        if previous and previous[-1].tool_name == "get_task_input_coverage":
            rejected = json.loads(previous[-1].content)
            assert rejected["status"] == "failed"
            assert rejected["error_code"] == "review_requires_read_task_input_snapshot"
        if previous and previous[-1].tool_name == "read_task_input_snapshot":
            page = json.loads(previous[-1].content)
            assert page["status"] == "completed", page
            pages.append(page["result"])
        if len(pages) < 2:
            return ModelResponse(
                tool_calls=[ToolCall(
                    id=f"read-fixed-{len(pages)}", name="read_task_input_snapshot",
                    arguments={"snapshot_id": reference["snapshot_id"],
                               "offset": len(pages), "limit": 1},
                )],
                finish_reason="tool_calls", latency_ms=1, raw_response={},
            )
        return ModelResponse(
            text=unit_review_text(request, "supported", "Checked the fixed candidate input."),
            finish_reason="stop", latency_ms=1, raw_response={},
        )

    provider.generate = generate
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)
    response = await client.post(f"/api/v1/runs/{turn['run']['run_id']}/agent-workflows",
                                 json=general_payload("always"))
    assert response.json()["status"] == "completed", response.text
    if not reads_pages:
        assert not pages and len(references) == 1
        return
    assert len(pages) == 2
    assert pages[0]["attempts"][0]["block_occurrences"] == 0
    assert pages[1]["attempts"][0]["block_occurrences"] == 1
    assert all(page["scope"] == "fixed_task_request_page" for page in pages)
    assert all(page["attempt_count"] == 2 for page in pages)
    assert all(page["task_id"] == references[0]["task_id"] for page in pages)
    assert pages[0]["next_offset"] == 1
    assert pages[1]["next_offset"] is None
    assert [page["offset"] for page in pages] == [0, 1]
    assert len({ref["snapshot_id"] for ref in references}) == 1


@pytest.fixture
async def captured_inputs(client):
    """Create one real candidate snapshot through API/tool/object persistence without a provider."""
    _, _, file, turn = await prepare_conversation_file(client)
    provider = GeneralProvider()

    async def generate(request):
        """Return a source-backed candidate after one actual file read."""
        provider.requests.append(request)
        if not any(m.role.value == "tool" for m in request.messages):
            return ModelResponse(
                tool_calls=[ToolCall(id="read", name="read_file_content",
                                     arguments={"file_id": file["file_id"], "limit": 1})],
                finish_reason="tool_calls", latency_ms=1, raw_response={},
            )
        return ModelResponse(text="Original content [S1].", finish_reason="stop",
                             latency_ms=1, raw_response={})

    provider.generate = generate
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)
    response = await client.post(f"/api/v1/runs/{turn['run']['run_id']}/agent-workflows",
                                 json=general_payload("never"))
    assert response.json()["status"] == "completed", response.text
    candidate = next(n["output"] for n in response.json()["nodes"]
                     if n["output_type"] == "general_candidate")
    return turn["run"]["run_id"], file, candidate["input_snapshot"]


async def test_captured_request_pages_do_not_change_when_more_attempts_arrive(
    client, captured_inputs, test_database_session_factory,
):
    """A newly inserted earlier-sorting attempt cannot shift fixed pages or alter a replay."""
    from datetime import datetime

    run_id, _, ref = captured_inputs
    storage = app.dependency_overrides[get_object_storage]()
    async with test_database_session_factory() as db:
        args = dict(run_id=run_id, requester_task_id=ref["task_id"],
                    snapshot_id=ref["snapshot_id"], allowed_snapshot_ids={ref["snapshot_id"]},
                    offset=0, limit=1)
        first = await read_task_input_snapshot(db, storage, **args)
        assert first["observation_kind"] == "candidate_request_set"
        assert first["request_list_observed_at"]
        db.add(LlmModelAttempt(
            attempt_id=new_id(), run_id=run_id, task_id=ref["task_id"],
            provider="openai", model="test-model", status=AttemptStatus.COMPLETED,
            started_at=datetime(2000, 1, 1),
        ))
        await db.commit()
        assert await read_task_input_snapshot(db, storage, **args) == first
        second = await read_task_input_snapshot(db, storage, **{**args, "offset": 1})
        assert second["attempt_count"] == 2 and second["next_offset"] is None
        count = len(storage.objects)
        replay = await save_task_input_snapshot(
            db, storage, run_id=run_id, task_id=ref["task_id"],
            candidate_node_id=ref["candidate_node_id"],
        )
        assert replay == ref
        assert len(storage.objects) == count


async def test_live_snapshot_report_detects_changed_request_bytes(
    client, captured_inputs, test_database_session_factory, monkeypatch,
):
    """The real-verification report checks request bytes, not merely stored snapshot metadata."""
    from pathlib import Path

    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[3]
                                   / "deployments" / "verification"))
    import check_live_general_agent as verification

    run_id, _, ref = captured_inputs
    storage = app.dependency_overrides[get_object_storage]()
    monkeypatch.setattr(verification, "database_session_factory", test_database_session_factory)
    monkeypatch.setattr(verification, "ObjectStorage", lambda: storage)
    records = await verification.candidate_input_snapshot_evidence(run_id)
    assert len(records) == 1 and records[0]["snapshot_id"] == ref["snapshot_id"]
    assert len(records[0]["attempts"]) == 2
    assert all(a["scope_matches"] and a["request_hash_matches"] for a in records[0]["attempts"])
    async with test_database_session_factory() as db:
        attempt = await db.get(LlmModelAttempt, records[0]["attempts"][0]["attempt_id"])
        raw, mime = storage.objects[attempt.raw_request_uri]
        storage.objects[attempt.raw_request_uri] = (raw + b" ", mime)
    changed = await verification.candidate_input_snapshot_evidence(run_id)
    assert changed[0]["snapshot_verified"] is True
    assert changed[0]["attempts"][0]["request_hash_matches"] is False


async def test_historical_input_snapshot_does_not_invent_observation_time(
    client, captured_inputs, test_database_session_factory,
):
    """A valid pre-timestamp snapshot remains readable with explicitly unknown observation time."""
    import hashlib

    run_id, _, ref = captured_inputs
    storage = app.dependency_overrides[get_object_storage]()
    async with test_database_session_factory() as db:
        row = await db.get(LlmMemorySnapshotObject, ref["snapshot_id"])
        raw, mime = storage.objects[row.storage_uri]
        envelope = json.loads(raw)
        envelope["content"].pop("request_list_observed_at")
        historical = json.dumps(envelope).encode()
        storage.objects[row.storage_uri] = (historical, mime)
        row.content_hash = hashlib.sha256(historical).hexdigest()
        row.size_bytes = len(historical)
        await db.commit()
        page = await read_task_input_snapshot(
            db, storage, run_id=run_id, requester_task_id=ref["task_id"],
            snapshot_id=ref["snapshot_id"], allowed_snapshot_ids={ref["snapshot_id"]},
            offset=0, limit=20,
        )
        assert page["request_list_observed_at"] is None
        assert page["observation_kind"] == "candidate_request_set"
        assert len(page["attempts"]) == 2


@pytest.mark.parametrize("failure", [
    "missing_grant", "different_run", "different_owner",
    "snapshot_hash", "request_hash", "revoked_file",
])
async def test_fixed_input_snapshot_refuses_unavailable_or_unauthorized_evidence(
    client, captured_inputs, test_database_session_factory, failure,
):
    """Refuse missing grants, foreign scope, corrupt objects and revoked files, not empty data."""
    run_id, file, ref = captured_inputs
    storage = app.dependency_overrides[get_object_storage]()
    async with test_database_session_factory() as db:
        args = dict(run_id=run_id, requester_task_id=ref["task_id"],
                    snapshot_id=ref["snapshot_id"], allowed_snapshot_ids={ref["snapshot_id"]},
                    offset=0, limit=20)
        snapshot = await db.get(LlmMemorySnapshotObject, ref["snapshot_id"])
        expected = ResourceNotFoundError
        if failure == "missing_grant":
            args["allowed_snapshot_ids"] = set()
        elif failure == "different_run":
            args["run_id"] = new_id()
        elif failure == "different_owner":
            user = User(user_id=new_id(), display_name="Other owner")
            db.add(user)
            await db.flush()
            snapshot.user_id = user.user_id
            await db.commit()
        elif failure in {"snapshot_hash", "request_hash"}:
            expected = ResourceConflictError
            uri = snapshot.storage_uri
            if failure == "request_hash":
                attempt = await db.scalar(select(LlmModelAttempt).where(
                    LlmModelAttempt.task_id == ref["task_id"],
                ).order_by(LlmModelAttempt.started_at).limit(1))
                uri = attempt.raw_request_uri
            _, mime = storage.objects[uri]
            storage.objects[uri] = (b"modified bytes", mime)
        else:
            record = await db.get(UserFile, file["file_id"])
            record.status = "deleted"
            await db.commit()
        with pytest.raises(expected):
            await read_task_input_snapshot(db, storage, **args)


async def test_candidate_input_snapshot_write_failure_prevents_review(
    client, test_database_session_factory, monkeypatch,
):
    """Do not publish an input reference or invoke Reviewer when snapshot verification fails."""
    from test_api import create_test_run

    from nexuspilot_api.features.agent_runtime.services.task_input_snapshot import SCHEMA

    run = await create_test_run(client)
    storage = app.dependency_overrides[get_object_storage]()
    original_verify = storage.verify_object

    async def reject_input_snapshot(uri, digest):
        """Fail only candidate input snapshots, preserving earlier execution observations."""
        if "task_inputs." in uri:
            return False
        return await original_verify(uri, digest)

    monkeypatch.setattr(storage, "verify_object", reject_input_snapshot)
    provider = GeneralProvider()
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)
    result = (await client.post(f"/api/v1/runs/{run['run_id']}/agent-workflows",
                               json=general_payload("always"))).json()
    assert result["status"] != "completed"
    assert result["final_output"] is None
    assert [r.metadata["agent_role"] for r in provider.requests] == ["controller"]
    async with test_database_session_factory() as db:
        assert list(await db.scalars(select(LlmMemorySnapshotObject).where(
            LlmMemorySnapshotObject.run_id == run["run_id"],
            LlmMemorySnapshotObject.schema_version == SCHEMA,
        ))) == []
