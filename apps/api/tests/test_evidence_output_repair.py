"""Exercise citation correction with original tool dialogue and real isolated persistence."""

import json

import pytest
from test_agent_workflows import (  # noqa: F401
    register_default_agent_test_model,
    scripted_registry,
    workflow_payload,
)
from test_conversation_file_context import prepare_conversation_file
from test_user_files import reserve_file
from test_worker_evidence import ClaimReviewProvider

from nexuspilot_api.core.dependencies import get_provider_registry
from nexuspilot_api.main import app


class IncorrectCitationProvider(ClaimReviewProvider):
    """Reproduce mixed read-call blocks, then correct them using preserved tool responses."""

    def __init__(self, file_id, *, persistent=False, snapshot_error=False):
        """Choose a correctable range mistake or a non-correctable snapshot mismatch."""
        super().__init__(file_id)
        self.persistent = persistent
        self.snapshot_error = snapshot_error
        self.repair_seen = False

    async def generate_checked(self, request):
        """Verify original evidence precedes the final correction instruction in actual requests."""
        response = await super().generate_checked(request)
        if request.metadata["agent_node_key"] != "independent_review" or response.tool_calls:
            return response
        repair = request.metadata.get("node_output_repair") == "1"
        if repair:
            self.repair_seen = True
            assert "allowed_block_ids" in str(request.messages)
            assert "Device model X200" in str(request.messages)
            assert any(m.role.value == "tool" for m in request.messages)
            assert request.tools == []
            assert request.messages[-1].role.value == "user"
            assert "output_repair" in request.messages[-1].content
        if not repair or self.persistent:
            output = response.structured_output
            ref = output["claim_assessments"][0]["evidence_refs"][0]
            if self.snapshot_error:
                ref["parsed_hash"] = "0" * 64
            else:
                ref["block_ids"] = ["line:1", "line:2"]
            return response.model_copy(update={"text": json.dumps(output)})
        return response


@pytest.mark.parametrize(
    "persistent,snapshot_error", [(False, False), (True, False), (False, True)]
)
async def test_reviewer_correction_preserves_sources_and_records_terminal_outcome(
    client,
    persistent,
    snapshot_error,
):
    """Correct one range error; never silently repair wrong snapshots or loop indefinitely."""
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
    provider = IncorrectCitationProvider(
        file["file_id"], persistent=persistent, snapshot_error=snapshot_error
    )
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)
    payload = workflow_payload()
    payload["workflow_version"] = "1.1.0"
    result = (
        await client.post(
            f"/api/v1/runs/{turn['run']['run_id']}/agent-workflows",
            json=payload,
        )
    ).json()
    assert result["status"] == ("failed" if persistent or snapshot_error else "completed"), (
        result.get("error"),
        getattr(provider, "fixture_error", None),
    )
    stats = result["loop_statistics"]
    assert stats["output_repair_count"] == int(not snapshot_error)
    assert stats["output_repair_failed_count"] == int(persistent)
    assert stats["output_repair_succeeded_count"] == int(not persistent and not snapshot_error)
    assert stats["output_repair_pending_count"] == 0
    assert stats["refinement_count"] == 0
    assert provider.repair_seen is (not snapshot_error)
    if not persistent and not snapshot_error:
        review = next(n for n in result["nodes"] if n["node_key"] == "independent_review")
        assert review["output"]["claim_assessments"][0]["evidence_refs"][0]["block_ids"] == [
            "line:2"
        ]
        assert review["usage"]["model_call_count"] == 3
        assert review["usage"]["tool_call_count"] == 1
        assert result["final_output"]["assistant_message_id"]
    reread = (
        await client.get(f"/api/v1/agent-workflows/{result['workflow_execution_id']}/result")
    ).json()
    assert reread["loop_statistics"] == stats


class FileRefinementProvider(ClaimReviewProvider):
    """Refine file tasks while keeping each round's claims and reviewer identities distinct."""

    async def generate_checked(self, request):
        """Use old evidence for planning but assess only current-round candidate tasks."""
        response = await super().generate_checked(request)
        round_number = request.metadata.get("workflow_round", "1")
        if request.metadata["agent_node_key"] == "controller_planning" and round_number == "2":
            assert "authorized_claim_sources" in str(request.messages)
            assert "Original text needs correction" in str(request.messages)
        if request.metadata["agent_node_key"] == "worker_execution" and round_number == "2":
            assert "refinement" in str(request.messages)
        if request.metadata["agent_node_key"] == "independent_review" and not response.tool_calls:
            output = response.structured_output
            output["requires_replan"] = round_number == "1"
            return response.model_copy(update={"text": json.dumps(output)})
        return response


async def test_two_file_rounds_keep_original_sources_and_current_candidate_associations(client):
    """Persist both read/review rounds and avoid demanding assessments for superseded tasks."""
    owner, session, _, _ = await prepare_conversation_file(client)
    content = b"Report introduction\nDevice model X200"
    file = (await reserve_file(client, content, "refine.txt")).json()
    await client.put(f"/api/v1/files/{file['file_id']}/content", content=content)
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
    provider = FileRefinementProvider(file["file_id"])
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)
    result = (
        await client.post(
            f"/api/v1/runs/{turn['run']['run_id']}/agent-workflows",
            json=workflow_payload(workflow_version="1.1.0"),
        )
    ).json()
    assert result["status"] == "completed", (
        result.get("error"),
        getattr(provider, "fixture_error", None),
    )
    assert result["loop_statistics"]["workflow_round_count"] == 2
    workers = [n for n in result["nodes"] if n["output_type"] == "agent_model_execution"]
    reviews = [n for n in result["nodes"] if n["output_type"] == "independent_review"]
    assert len(workers) == len(reviews) == 2
    assert workers[0]["task_id"] != workers[1]["task_id"]
    for worker, reviewer in zip(workers, reviews, strict=True):
        assert len(reviewer["output"]["claim_assessments"]) == 1
        assert reviewer["output"]["claim_assessments"][0]["task_id"] == worker["task_id"]
        sources = (
            await client.get(
                f"/api/v1/agent-workflows/{result['workflow_execution_id']}/nodes/{worker['node_execution_id']}/claim-sources",
                params={"claim_key": "equipment-model"},
            )
        ).json()
        assert [s["read_by"] for s in sources["sources"]] == ["worker", "reviewer"]
    assert result["aggregate_usage"]["model_call_count"] == 11
    assert result["aggregate_usage"]["tool_call_count"] == 4
