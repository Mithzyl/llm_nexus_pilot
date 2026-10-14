"""Verify pinned file evidence and reviewer reads through real test persistence."""

import json
from types import SimpleNamespace

import pytest
from nexuspilot_models.contracts import ModelResponse, ToolCall
from sqlalchemy import select
from test_agent_file_tools import FileToolProvider
from test_agent_workflows import (
    ScriptedAgentProvider,
    register_default_agent_test_model,  # noqa: F401
    scripted_registry,
    workflow_payload,
)
from test_conversation_file_context import prepare_conversation_file

from nexuspilot_api.core.dependencies import get_provider_registry
from nexuspilot_api.core.errors import InvalidRequestError, ResourceConflictError
from nexuspilot_api.infrastructure.object_storage import get_object_storage
from nexuspilot_api.main import app
from nexuspilot_api.models import LlmModelAttempt, LlmModelToolCall, UserFile
from nexuspilot_api.schemas.worker_evidence import FileEvidenceReference, WorkerEvidenceBundle
from nexuspilot_api.services.worker_evidence_service import WorkerEvidenceService


@pytest.mark.parametrize("fault", ["omitted", "duplicate", "invented", "unsupported_pass"])
async def test_review_rejects_incomplete_or_false_evidence_coverage(
    evidence_read, test_database_session_factory, fault
):
    """Validate each assessment against persisted ancestor claims, not reviewer assertions."""
    from nexuspilot_api.features.agent_runtime.schemas.agent_workflows import (
        EvidenceReviewerModelOutput,
    )
    from nexuspilot_api.features.agent_runtime.services.agent_model_node_service import (
        AgentModelNodeService,
    )
    from nexuspilot_api.models import LlmAgentWorkflowNodeExecution

    run_id, task_id, reference, _ = evidence_read
    async with test_database_session_factory() as db:
        worker = await db.scalar(
            select(LlmAgentWorkflowNodeExecution).where(
                LlmAgentWorkflowNodeExecution.task_id == task_id,
                LlmAgentWorkflowNodeExecution.output_type == "agent_model_execution",
            )
        )
        output = dict(worker.output_json)
        structured = dict(output["structured_output"])
        structured["claims"] = [
            {
                "claim_key": "introduction",
                "text": "Report introduction",
                "evidence_refs": [reference],
            }
        ]
        output["structured_output"] = structured
        worker.output_json = output
        await db.flush()
        reviewer = await db.scalar(
            select(LlmAgentWorkflowNodeExecution).where(
                LlmAgentWorkflowNodeExecution.run_id == run_id,
                LlmAgentWorkflowNodeExecution.node_key == "independent_review",
            )
        )
        raw = dict(reviewer.output_json)
        raw.pop("schema_version", None)
        raw.pop("output_type", None)
        # Use the actual accepted reviewer fields from the persisted result.
        raw = {k: v for k, v in raw.items() if k in EvidenceReviewerModelOutput.model_fields}
        assessment = {
            "task_id": task_id,
            "claim_key": "introduction",
            "verdict": "supported",
            "explanation": "Read the original",
            "evidence_refs": [reference],
        }
        if fault == "invented":
            assessment["evidence_refs"] = [{**reference, "result_id": "fabricated"}]
        if fault == "unsupported_pass":
            assessment.update(verdict="insufficient_evidence", evidence_refs=[])
            raw["verdict"] = "pass"
        raw["claim_assessments"] = (
            []
            if fault == "omitted"
            else [assessment, assessment]
            if fault == "duplicate"
            else [assessment]
        )
        parsed = EvidenceReviewerModelOutput.model_validate(raw)
        service = AgentModelNodeService.__new__(AgentModelNodeService)
        service.model_invocation_service = SimpleNamespace(
            db_session=db, storage=app.dependency_overrides[get_object_storage]()
        )
        with pytest.raises(InvalidRequestError):
            await service._validate_evidence_output(
                SimpleNamespace(run_id=run_id), reviewer, parsed
            )


async def test_read_result_exposes_pinned_citable_blocks(client, test_database_session_factory):
    """A citation identifies the persisted call, exact parse hash and returned block range."""
    _, _, file, turn = await prepare_conversation_file(client)
    provider = FileToolProvider(
        tool_name="read_file_content", arguments={"file_id": file["file_id"]}
    )
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)
    response = await client.post(
        f"/api/v1/runs/{turn['run']['run_id']}/agent-workflows", json=workflow_payload()
    )
    assert response.json()["status"] == "completed", response.text
    async with test_database_session_factory() as db:
        call = await db.scalar(
            select(LlmModelToolCall).where(LlmModelToolCall.tool_name == "read_file_content")
        )
        source = call.output_json["result"]
        assert source["evidence_ref"]["tool_call_id"] == call.tool_call_id
        assert source["evidence_ref"]["parsed_hash"]
        assert source["evidence_ref"]["block_ids"] == ["line:1"]
        assert source["read_scope"]["has_more"] is False
        assert source["read_scope"]["kind"] == "read_file_content"


@pytest.fixture
async def evidence_read(client, test_database_session_factory):
    """Persist a two-block file but expose only the first block through the Worker tool."""
    from test_user_files import reserve_file

    owner, session, _, _ = await prepare_conversation_file(client)
    content = b"Report introduction\nDevice model X200"
    file = (await reserve_file(client, content, "equipment.txt")).json()
    assert (
        await client.put(f"/api/v1/files/{file['file_id']}/content", content=content)
    ).status_code == 200
    turn = (
        await client.post(
            f"/api/v1/sessions/{session['session_id']}/turns",
            json={
                "user_id": owner,
                "content_text": "Find the equipment model",
                "file_ids": [file["file_id"]],
            },
        )
    ).json()
    provider = FileToolProvider(
        tool_name="read_file_content", arguments={"file_id": file["file_id"], "limit": 1}
    )
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)
    result = (
        await client.post(
            f"/api/v1/runs/{turn['run']['run_id']}/agent-workflows", json=workflow_payload()
        )
    ).json()
    assert result["status"] == "completed", result.get("error")
    async with test_database_session_factory() as db:
        record = await db.scalar(
            select(LlmModelToolCall).where(LlmModelToolCall.tool_name == "read_file_content")
        )
        attempt = await db.get(LlmModelAttempt, record.attempt_id)
        return (
            turn["run"]["run_id"],
            attempt.task_id,
            record.output_json["result"]["evidence_ref"],
            file["file_id"],
        )


@pytest.mark.parametrize(
    "mutation",
    [
        "call",
        "result",
        "file",
        "hash",
        "parse",
        "version",
        "block",
        "task",
        "run",
        "deleted",
        "reassigned",
        "snapshot_changed",
        "missing_object",
    ],
)
async def test_evidence_rejects_forged_or_inaccessible_source(
    evidence_read, test_database_session_factory, mutation
):
    """Do not accept known IDs without exact snapshot, read range and current resource access."""
    run_id, task_id, raw, file_id = evidence_read
    raw = dict(raw)
    changes = {
        "call": {"tool_call_id": "00000000-0000-0000-0000-000000000000"},
        "result": {"result_id": "invented"},
        "file": {"file_id": "00000000-0000-0000-0000-000000000000"},
        "hash": {"source_hash": "0" * 64},
        "parse": {"parsed_hash": "0" * 64},
        "version": {"parser_version": "different"},
        "block": {"block_ids": ["line:2"]},
    }
    raw.update(changes.get(mutation, {}))
    storage = app.dependency_overrides[get_object_storage]()
    async with test_database_session_factory() as db:
        file = await db.get(UserFile, file_id)
        if mutation == "deleted":
            file.status = "deleting"
        if mutation == "reassigned":
            from nexuspilot_api.models import User

            db.add(User(user_id="other-evidence-owner", display_name="Other owner"))
            await db.flush()
            file.user_id = "other-evidence-owner"
        if mutation == "snapshot_changed":
            file.parsed_hash = "0" * 64
        if mutation == "missing_object":
            storage.objects.pop(file.parsed_uri)
        await db.commit()
        with pytest.raises((InvalidRequestError, ResourceConflictError)):
            await WorkerEvidenceService(db, storage).resolve(
                "00000000-0000-0000-0000-000000000000" if mutation == "run" else run_id,
                set() if mutation == "task" else {task_id},
                FileEvidenceReference.model_validate(raw),
            )


async def test_reference_returns_original_not_model_summary(
    evidence_read, test_database_session_factory
):
    """Return the read paragraph and pagination without assuming full coverage."""
    run_id, task_id, raw, _ = evidence_read
    async with test_database_session_factory() as db:
        resolver = WorkerEvidenceService(db, app.dependency_overrides[get_object_storage]())
        source = await resolver.resolve(
            run_id, {task_id}, FileEvidenceReference.model_validate(raw)
        )
        assert source["blocks"][0]["text"] == "Report introduction\n"
        assert source["read_scope"]["has_more"] is True
        assert source["content_verified"] is False
        bundle = WorkerEvidenceBundle(
            claims=[
                {
                    "claim_key": "no-model",
                    "text": "No equipment model",
                    "kind": "absence",
                    "scope": "parsed_document",
                    "evidence_refs": [raw],
                }
            ]
        )
        with pytest.raises(InvalidRequestError, match="read range"):
            await resolver.resolve_bundle(run_id, {task_id}, bundle)


class ClaimReviewProvider(FileToolProvider):
    """Submit a wrong summary, then require originals and a supplementary review read."""

    def __init__(self, file_id, *, refuse_more=False):
        """Select the same file for initial and supplementary reads without an external provider."""
        super().__init__(tool_name="read_file_content", arguments={"file_id": file_id, "limit": 1})
        self.file_id = file_id
        self.refuse_more = refuse_more

    async def generate(self, request):
        """Expose fixture assertions that provider error conversion would otherwise hide."""
        try:
            return await self.generate_checked(request)
        except Exception as exc:
            self.fixture_error = repr(exc)
            raise

    async def generate_checked(self, request):
        """Require actual original excerpts rather than relying on the inaccurate Worker summary."""
        node_key = request.metadata["agent_node_key"]
        if node_key == "independent_review":
            inputs = json.loads(
                next(m.content for m in reversed(request.messages) if m.role.value == "user")
            )
            sources = inputs["authorized_claim_sources"]
            claim = sources[0]["claims"][0]
            assert claim["sources"][0]["blocks"][0]["text"] == "Report introduction\n"
            assert claim["sources"][0]["read_scope"]["has_more"] is True
            previous = [m for m in request.messages if m.role.value == "tool"]
            if not previous and not self.refuse_more:
                self.requests.append(request)
                return ModelResponse(
                    tool_calls=[
                        ToolCall(
                            id="review-read",
                            name="read_file_content",
                            arguments={"file_id": self.file_id, "offset": 1, "limit": 1},
                        )
                    ],
                    finish_reason="tool_calls",
                    input_tokens=10,
                    output_tokens=5,
                    latency_ms=1,
                    raw_response={"fixture": "review-read"},
                )
            response = await ScriptedAgentProvider.generate(self, request)
            output = response.structured_output
            if previous:
                read = json.loads(previous[-1].content)["result"]
                assert read["data"]["blocks"][0]["text"] == "Device model X200"
                refs = [read["evidence_ref"]]
            else:
                refs = []
            output.update(
                verdict="needs_revision",
                review_summary="Original text needs correction",
                rejected_claims=[claim["text"]],
                accepted_claims=[],
                claim_assessments=[
                    {
                        "task_id": sources[0]["task_id"],
                        "claim_key": claim["claim_key"],
                        "verdict": "contradicted" if previous else "insufficient_evidence",
                        "explanation": "The second block specifies X200"
                        if previous
                        else "Only first block was read",
                        "evidence_refs": refs,
                    }
                ],
            )
            return response.model_copy(update={"text": json.dumps(output)})
        response = await super().generate(request)
        if node_key == "worker_execution" and not response.tool_calls:
            read = json.loads(
                next(m.content for m in reversed(request.messages) if m.role.value == "tool")
            )["result"]
            output = response.structured_output
            output.update(
                summary="No equipment model was supplied",
                claims=[
                    {
                        "claim_key": "equipment-model",
                        "text": "The document gives no equipment model",
                        "source_type": "file",
                        "kind": "absence",
                        "scope": "read_blocks",
                        "evidence_refs": [read["evidence_ref"]],
                    }
                ],
            )
            return response.model_copy(update={"text": json.dumps(output)})
        return response


@pytest.mark.parametrize("refuse_more", [False, True])
async def test_handoff_retains_claims_and_reviewer_uses_originals(
    client, test_database_session_factory, refuse_more
):
    """The reviewer request receives stored original evidence and can read missing blocks itself."""
    from test_user_files import reserve_file

    from nexuspilot_api.models import LlmAgentHandoff

    owner, session, _, _ = await prepare_conversation_file(client)
    content = b"Report introduction\nDevice model X200"
    file = (await reserve_file(client, content, "equipment.txt")).json()
    assert (
        await client.put(f"/api/v1/files/{file['file_id']}/content", content=content)
    ).status_code == 200
    turn = (
        await client.post(
            f"/api/v1/sessions/{session['session_id']}/turns",
            json={
                "user_id": owner,
                "content_text": "Find equipment model",
                "file_ids": [file["file_id"]],
            },
        )
    ).json()
    provider = ClaimReviewProvider(file["file_id"], refuse_more=refuse_more)
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)
    result = (
        await client.post(
            f"/api/v1/runs/{turn['run']['run_id']}/agent-workflows", json=workflow_payload()
        )
    ).json()
    assert result["status"] == "completed", (
        result.get("error"),
        getattr(provider, "fixture_error", None),
    )
    worker = next(n for n in result["nodes"] if n["node_key"].startswith("worker_execution."))
    review = next(n for n in result["nodes"] if n["node_key"] == "independent_review")
    assert review["output"]["claim_assessments"][0]["verdict"] == (
        "insufficient_evidence" if refuse_more else "contradicted"
    )
    assert review["usage"]["tool_call_count"] == (0 if refuse_more else 1)
    assert review["usage"]["model_call_count"] == (1 if refuse_more else 2)
    sources_response = await client.get(
        f"/api/v1/agent-workflows/{result['workflow_execution_id']}/nodes/"
        f"{worker['node_execution_id']}/claim-sources",
        params={"claim_key": "equipment-model"},
    )
    assert sources_response.status_code == 200, sources_response.text
    sources = sources_response.json()["sources"]
    assert sources[0]["read_by"] == "worker"
    if not refuse_more:
        assert sources[1]["read_by"] == "reviewer"
        assert sources[1]["blocks"][0]["text"] == "Device model X200"
    async with test_database_session_factory() as db:
        handoff = await db.scalar(
            select(LlmAgentHandoff).where(LlmAgentHandoff.run_id == turn["run"]["run_id"])
        )
        assert handoff.schema_version == "agent_handoff.v2"
        assert (
            handoff.handoff_json["worker_evidence"]["claims"]
            == worker["output"]["structured_output"]["claims"]
        )
    assert (
        await client.get(f"/api/v1/agent-workflows/{result['workflow_execution_id']}/result")
    ).json() == result
