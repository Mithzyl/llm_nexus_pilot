"""Verify native reviewer submission through actual local workflow/tool persistence."""

import asyncio
import copy
import json

import pytest
from nexuspilot_models.contracts import ModelResponse, ToolCall
from test_agent_workflows import register_default_agent_test_model, scripted_registry  # noqa: F401
from test_api import create_test_run
from test_general_agent import (  # noqa: F401
    GeneralProvider,
    general_model_catalog,
    general_payload,
    unit_review_text,
)

from nexuspilot_api.core.dependencies import get_provider_registry
from nexuspilot_api.main import app


class SubmittingReviewer(GeneralProvider):
    """Submit native decisions once, with an optional invalid first submission."""

    def __init__(self, invalid=False, multi_batch=False, revise_once=False):
        """Configure invalid fields, consecutive batches, or one content revision."""
        super().__init__()
        self.invalid = invalid
        self.multi_batch = multi_batch
        self.revise_once = revise_once

    async def generate(self, request):
        """Check role-scoped availability and return native arguments, not fabricated body JSON."""
        if request.metadata["agent_role"] != "reviewer":
            assert "submit_review" not in {tool.name for tool in request.tools}
            return await super().generate(request)
        self.requests.append(request)
        assert request.output_schema is None and not request.json_object_output
        self.reviews += 1
        if self.reviews > 1 and not self.multi_batch:
            assert self.invalid, "A valid native submission must not need another model call"
            assert not request.tools
            assert request.messages[-1].role.value == "user"
            expected = json.loads(unit_review_text(request, "supported", "Repaired"))
            # Existing output repair puts its fresh input after old tool history.
            latest = json.loads(request.messages[-1].content)["candidate_context"]["review_units"]
            assert [unit["unit_id"] for unit in latest] == [
                decision["unit_id"] for decision in expected["decisions"]
            ]
            return ModelResponse(
                text=unit_review_text(request, "supported", "Repaired"),
                finish_reason="stop", latency_ms=1, raw_response={},
            )
        assert "submit_review" in {tool.name for tool in request.tools}
        verdict = "revise" if self.revise_once and self.reviews == 1 else "supported"
        args = json.loads(unit_review_text(request, verdict, "Correct unsupported claims"))
        schema = next(t.input_schema for t in request.tools if t.name == "submit_review")
        assert schema["$defs"]["GeneralUnitReview"]["properties"]["unit_id"]["enum"] == [
            decision["unit_id"] for decision in args["decisions"]
        ]
        if self.invalid is True or self.invalid == "unknown_unit":
            args["decisions"][0]["unit_id"] = "u999"
        elif self.invalid == "unknown_source":
            args["decisions"][0]["source_aliases"] = ["S999"]
        elif self.invalid == "duplicate":
            args["decisions"].append(copy.deepcopy(args["decisions"][0]))
        elif self.invalid == "missing":
            args["decisions"] = []
        elif self.invalid == "wrong_verdict":
            args["decisions"][0]["verdict"] = "approved"
        elif self.invalid == "foreign_scope":
            args["task_id"] = "model-controlled-task"
        return ModelResponse(
            tool_calls=[ToolCall(
                id=f"review-{self.reviews}", name="submit_review", arguments=args,
            )],
            finish_reason="tool_calls", input_tokens=10, output_tokens=5,
            latency_ms=1, raw_response={"text": None},
        )


@pytest.mark.parametrize("invalid", [
    False, "unknown_unit", "unknown_source", "duplicate", "missing",
    "wrong_verdict", "foreign_scope",
])
async def test_native_review_stops_after_persisted_submission_or_repairs_once(
    client, invalid, test_database_session_factory, monkeypatch,
):
    """Verify immediate submission, finite repair, replay and object-backed tool bodies."""
    provider = SubmittingReviewer(invalid)
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)
    run = await create_test_run(client)
    payload = general_payload("always")
    result = (await client.post(
        f"/api/v1/runs/{run['run_id']}/agent-workflows", json=payload,
    )).json()
    assert result["status"] == "completed", result.get("error")
    assert provider.reviews == (2 if invalid else 1)
    assert result["model_call_count"] == 2 + int(bool(invalid))
    calls = [call for node in result["nodes"] for call in node["evidence"]["tool_results"]
             if call["tool_name"] == "submit_review"]
    assert len(calls) == 1
    assert calls[0]["status"] == ("failed" if invalid else "completed")
    assert result["loop_statistics"]["output_repair_count"] == int(bool(invalid))
    count = len(provider.requests)
    replay = (await client.post(
        f"/api/v1/runs/{run['run_id']}/agent-workflows", json=payload,
    )).json()
    assert replay["final_output"] == result["final_output"]
    assert len(provider.requests) == count
    from pathlib import Path

    from nexuspilot_api.infrastructure.object_storage import get_object_storage

    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[3]
                                   / "deployments" / "verification"))
    import check_live_general_agent as verification

    monkeypatch.setattr(verification, "database_session_factory", test_database_session_factory)
    monkeypatch.setattr(verification, "ObjectStorage", app.dependency_overrides[get_object_storage])
    evidence = await verification.storage_evidence(run["run_id"])
    saved = [item for item in evidence if item["table"] == "llm_tool_calls"]
    assert len(saved) == 1
    assert saved[0]["sql_body_empty"] and saved[0]["sql_input_empty"]
    assert saved[0]["object_present"] and saved[0]["hash_verified"]
    if not invalid:
        from nexuspilot_api.models import LlmModelToolCall

        storage = app.dependency_overrides[get_object_storage]()
        async with test_database_session_factory() as db:
            record = await db.get(LlmModelToolCall, saved[0]["id"])
            uri = record.body_ref_json["uri"]
        body, mime = storage.objects[uri]
        storage.objects[uri] = (body + b"\n", mime)
        changed = await verification.storage_evidence(run["run_id"])
        assert not next(item for item in changed if item["id"] == saved[0]["id"])["hash_verified"]


async def test_mixed_submission_executes_no_tools(client):
    """Reject submission-plus-side-effect batches before saving a note or approving a review."""
    provider = SubmittingReviewer()
    generate = provider.generate

    async def mixed(request):
        """Append a valid write to a native submission to exercise pre-execution rejection."""
        response = await generate(request)
        if request.metadata["agent_role"] == "reviewer":
            response.tool_calls.append(ToolCall(
                id="unexpected-write", name="save_task_note", arguments={"note": "must not save"},
            ))
        return response

    provider.generate = mixed
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)
    run = await create_test_run(client)
    result = (await client.post(
        f"/api/v1/runs/{run['run_id']}/agent-workflows", json=general_payload("always"),
    )).json()
    assert result["status"] == "failed"
    assert result["final_output"] is None
    assert not [call for node in result["nodes"] for call in node["evidence"]["tool_results"]]


@pytest.mark.parametrize("failure", ["persistence", "cancel"])
async def test_submission_cannot_complete_before_persistence_and_stop_check(
    client, monkeypatch, failure,
):
    """A failed save or cancellation at submission cannot become a successful final answer."""
    from nexuspilot_api.services.agent_file_tool_service import AgentFileToolService

    finish = AgentFileToolService._finish

    async def fail_submission(self, record, status, **kwargs):
        """Interrupt only successful submit_review persistence; other bookkeeping remains real."""
        if record.tool_name == "submit_review" and status == "completed":
            if failure == "cancel":
                raise asyncio.CancelledError()
            raise RuntimeError("simulated review storage failure")
        return await finish(self, record, status, **kwargs)

    monkeypatch.setattr(AgentFileToolService, "_finish", fail_submission)
    provider = SubmittingReviewer()
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)
    run = await create_test_run(client)
    call = client.post(
        f"/api/v1/runs/{run['run_id']}/agent-workflows", json=general_payload("always"),
    )
    if failure == "cancel":
        with pytest.raises(asyncio.CancelledError):
            await call
        summary = (await client.get(f"/api/v1/runs/{run['run_id']}/agent-workflow")).json()
        result = (await client.get(
            f"/api/v1/agent-workflows/{summary['workflow_execution_id']}/result",
        )).json()
        assert result["status"] == "cancelled"
    else:
        result = (await call).json()
    assert result["status"] != "completed"
    assert result["final_output"] is None
    assert provider.reviews == 1


@pytest.mark.parametrize("policy", ["never", "on_verification_failure"])
async def test_submission_does_not_enable_unrequested_review(client, policy):
    """Plain answers retain no-review policy behavior and hide submission capability."""
    provider = SubmittingReviewer()
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)
    run = await create_test_run(client)
    result = (await client.post(
        f"/api/v1/runs/{run['run_id']}/agent-workflows", json=general_payload(policy),
    )).json()
    assert result["status"] == "completed"
    assert provider.reviews == 0


async def test_native_submission_preserves_all_batches(client):
    """Each native batch ends without a follow-up generation and keeps its own allowed IDs."""
    provider = SubmittingReviewer(multi_batch=True)
    generate = provider.generate

    async def many_units(request):
        """Verify the second assignment follows the first submission in actual message order."""
        response = await generate(request)
        if request.metadata["agent_role"] == "controller":
            response.text = "\n\n".join(f"Paragraph {index}." for index in range(7))
        elif provider.reviews == 2:
            assert request.messages[-1].role.value == "user"
            assert json.loads(request.messages[-1].content)["review_unit_ids"] == ["u7"]
        return response

    provider.generate = many_units
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)
    run = await create_test_run(client)
    result = (await client.post(
        f"/api/v1/runs/{run['run_id']}/agent-workflows", json=general_payload("always"),
    )).json()
    assert result["status"] == "completed", result.get("error")
    assert provider.reviews == 2
    assert result["model_call_count"] == 3
    review = next(node["output"] for node in result["nodes"]
                  if node["output_type"] == "general_review")
    assert [item["unit_id"] for item in review["decisions"]] == [f"u{i}" for i in range(1, 8)]


async def test_controller_cannot_execute_unoffered_submission(client):
    """A controller attempting the hidden tool receives a persisted denial, never approval."""
    provider = GeneralProvider()

    async def attempt_submission(request):
        """Attempt the unoffered tool once, then return ordinary prose after its denial."""
        provider.requests.append(request)
        assert "submit_review" not in {tool.name for tool in request.tools}
        previous = [m for m in request.messages if m.role.value == "tool"]
        if not previous:
            return ModelResponse(
                tool_calls=[ToolCall(id="forbidden", name="submit_review", arguments={
                    "decisions": [{"unit_id": "u1", "verdict": "supported", "feedback": "fake"}],
                })], finish_reason="tool_calls", latency_ms=1, raw_response={},
            )
        assert json.loads(previous[-1].content)["status"] == "permission_denied"
        return ModelResponse(text="Ordinary answer.", finish_reason="stop",
                             latency_ms=1, raw_response={})

    provider.generate = attempt_submission
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)
    run = await create_test_run(client)
    result = (await client.post(
        f"/api/v1/runs/{run['run_id']}/agent-workflows", json=general_payload("never"),
    )).json()
    assert result["status"] == "completed", result.get("error")
    assert not any(n["output_type"] == "general_review" for n in result["nodes"])


@pytest.mark.parametrize("target", ["different_task", "different_run", "unlinked_candidate"])
async def test_native_submission_rechecks_task_and_candidate_scope(
    client, test_database_session_factory, target,
):
    """Even a wrongly constructed internal service cannot submit another task's or Run's review."""
    from sqlalchemy import select

    from nexuspilot_api.features.agent_runtime.services.task_note_tools import (
        GeneralAgentToolService,
    )
    from nexuspilot_api.infrastructure.object_storage import get_object_storage
    from nexuspilot_api.models import (
        AgentWorkflowNodeStatus,
        LlmAgentWorkflowNodeExecution,
        LlmModelAttempt,
        LlmRun,
        LlmTask,
        RunStatus,
        TaskStatus,
    )

    results = []
    providers = []
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(providers[-1])
    for _ in range(2):
        providers.append(SubmittingReviewer())
        run = await create_test_run(client)
        result = (await client.post(
            f"/api/v1/runs/{run['run_id']}/agent-workflows", json=general_payload("always"),
        )).json()
        assert result["status"] == "completed", result.get("error")
        results.append(result)
    result, foreign = results
    review = next(n for n in result["nodes"] if n["output_type"] == "general_review")
    candidate = next(n for n in result["nodes"] if n["output_type"] == "general_candidate")
    foreign_candidate = next(n for n in foreign["nodes"] if n["output_type"] == "general_candidate")
    async with test_database_session_factory() as db:
        # Test fixture: restore execution eligibility to isolate the identity checks themselves.
        run = await db.get(LlmRun, result["run_id"])
        run.status = RunStatus.RUNNING
        task = await db.get(LlmTask, review["task_id"])
        task.status = TaskStatus.RUNNING
        sibling = await db.get(LlmTask, candidate["task_id"])
        sibling.status = TaskStatus.RUNNING
        review_node = await db.get(LlmAgentWorkflowNodeExecution, review["node_execution_id"])
        review_node.status = AgentWorkflowNodeStatus.RUNNING
        if target == "unlinked_candidate":
            review_node.input_json = {**review_node.input_json, "source_node_execution_ids": []}
        await db.commit()
        attempt = await db.scalar(select(LlmModelAttempt).where(
            LlmModelAttempt.task_id == task.task_id,
        ))
        tools = GeneralAgentToolService(
            db, app.dependency_overrides[get_object_storage](),
            task_id=sibling.task_id if target == "different_task" else task.task_id,
            sources={}, allowed_task_ids={task.task_id},
            review_candidate_id=(foreign_candidate if target == "different_run" else candidate)[
                "node_execution_id"
            ], review_unit_ids=["u1"],
        )
        denied = await tools.execute(
            attempt_id=attempt.attempt_id,
            call=ToolCall(id="wrong-scope", name="submit_review", arguments={
                "decisions": [{"unit_id": "u1", "verdict": "supported", "feedback": "fake"}],
            }),
        )
        assert denied.status == "permission_denied"
        assert denied.error_code == (
            "task_not_running" if target == "different_task" else "review_submission_not_allowed"
        )


@pytest.mark.parametrize("policy,review_count", [("always", 2), ("on_verification_failure", 1)])
async def test_native_review_refinement_returns_to_main_and_preserves_policy(
    client, policy, review_count,
):
    """A native revise decision returns to main, while the original review policy remains fixed."""
    provider = SubmittingReviewer(multi_batch=True, revise_once=True)
    generate = provider.generate

    async def repair_source(request):
        """Produce one unsupported source and remove it only after actual reviewer feedback."""
        response = await generate(request)
        if request.metadata["agent_role"] == "controller" and not provider.reviews:
            response.text += " [S999]"
        return response

    provider.generate = repair_source
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)
    run = await create_test_run(client)
    result = (await client.post(
        f"/api/v1/runs/{run['run_id']}/agent-workflows", json=general_payload(policy),
    )).json()
    assert result["status"] == "completed", result.get("error")
    assert provider.reviews == review_count
    assert result["model_call_count"] == review_count + 2
    assert result["loop_statistics"]["workflow_round_count"] == 2
    assert result["loop_statistics"]["refinement_count"] == 1
    assert result["loop_statistics"]["output_repair_count"] == 0
    assert "[S999]" not in result["final_output"]["final_text"]


@pytest.mark.parametrize("missing_review_sources", [False, True])
@pytest.mark.parametrize("revoke_file", [False, True])
async def test_native_review_keeps_real_file_sources_and_rechecks_access(
    client, test_database_session_factory, revoke_file, missing_review_sources,
):
    """Require explicit native source approval, finite repair and current file authorization."""
    from test_conversation_file_context import prepare_conversation_file

    from nexuspilot_api.models import UserFile

    _, _, file, turn = await prepare_conversation_file(client)
    provider = SubmittingReviewer(
        invalid="missing_cited_sources" if missing_review_sources else False,
    )
    generate = provider.generate

    async def read_then_review(request):
        """Read one actual file block before submission and optionally revoke it during review."""
        if request.metadata["agent_role"] == "controller":
            provider.requests.append(request)
            if not any(m.role.value == "tool" for m in request.messages):
                return ModelResponse(
                    tool_calls=[ToolCall(id="read", name="read_file_content",
                                         arguments={"file_id": file["file_id"], "limit": 1})],
                    finish_reason="tool_calls", latency_ms=1, raw_response={},
                )
            return ModelResponse(text="The attached original is available [S1].",
                                 finish_reason="stop", latency_ms=1, raw_response={})
        if not request.tools:
            return await generate(request)
        schema = next(t.input_schema for t in request.tools if t.name == "submit_review")
        assert schema["$defs"]["GeneralUnitReview"]["properties"]["source_aliases"]["items"][
            "enum"
        ] == ["S1"]
        if revoke_file:
            async with test_database_session_factory() as db:
                row = await db.get(UserFile, file["file_id"])
                row.status = "deleted"
                await db.commit()
        response = await generate(request)
        if missing_review_sources:
            response.tool_calls[0].arguments["decisions"][0]["source_aliases"] = []
        return response

    provider.generate = read_then_review
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)
    result = (await client.post(
        f"/api/v1/runs/{turn['run']['run_id']}/agent-workflows", json=general_payload("always"),
    )).json()
    if revoke_file:
        assert result["status"] != "completed"
        assert result["final_output"] is None
    else:
        assert result["status"] == "completed", result.get("error")
        review = next(n["output"] for n in result["nodes"] if n["output_type"] == "general_review")
        assert review["decisions"][0]["source_aliases"] == ["S1"]
        assert review["sources"]["S1"]["file_id"] == file["file_id"]
        assert result["loop_statistics"]["output_repair_count"] == int(missing_review_sources)
        from sqlalchemy import select

        from nexuspilot_api.models import LlmModelToolCall

        async with test_database_session_factory() as db:
            submission = await db.scalar(select(LlmModelToolCall).where(
                LlmModelToolCall.tool_name == "submit_review",
            ))
            assert submission.status == ("failed" if missing_review_sources else "completed")
            assert submission.error_code == (
                "invalid_review_submission" if missing_review_sources else None
            )
