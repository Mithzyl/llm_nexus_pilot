"""Exercise the opt-in general loop through API persistence without paid model requests."""

import json

import pytest
from nexuspilot_models.contracts import ModelResponse, ToolCall
from sqlalchemy import select
from test_agent_workflows import (
    ScriptedAgentProvider,
    register_default_agent_test_model,  # noqa: F401
    scripted_registry,
    workflow_payload,
)
from test_api import create_test_run

from nexuspilot_api.core.dependencies import get_provider_registry
from nexuspilot_api.main import app


@pytest.fixture(autouse=True)
async def general_model_catalog(client):
    """Register context capacity for session-anchored requests as well as direct Runs."""
    from test_agent_workflows import INTERNAL_HEADERS

    response = await client.post(
        "/api/v1/internal/model-catalog-versions",
        headers=INTERNAL_HEADERS,
        json={
            "provider": "openai",
            "model": "test-model",
            "catalog_version": "general-test",
            "source": "general-agent-test",
            "context_window": 131072,
            "supports_streaming": True,
            "tokenizer_name": "utf8_bytes_upper_bound",
            "tokenizer_version": "v1",
        },
    )
    assert response.status_code == 200, response.text


def general_payload(policy="never", **overrides):
    """Opt into a separate definition while retaining existing role model bindings."""
    payload = workflow_payload(
        workflow_name="general_agent",
        workflow_version="1.0.0",
        execution_profile="general_tools_v1",
        review_policy=policy,
    )
    payload["role_bindings"].pop("researcher")
    payload.update(overrides)
    return payload


def requested_review_units(request):
    """Read only the explicitly requested batch from the actual serialized model input."""
    for message in request.messages:
        try:
            value = json.loads(message.content or "")
        except (ValueError, TypeError):
            continue
        if isinstance(value, dict):
            value = value.get("candidate_context", value)
            if "review_units" in value:
                assert "candidate_units" not in value, "Do not expose other batch IDs to the model"
                assert all(
                    set(unit) == {"unit_id", "kind", "text", "source_aliases"}
                    for unit in value["review_units"]
                )
                return value["review_units"]
    raise AssertionError("Reviewer did not receive an explicit unit batch")


def unit_review_text(request, verdict, feedback):
    """Generate fixture decisions for exactly the requested units, preserving their short refs."""
    return json.dumps(
        {
            "decisions": [
                {
                    "unit_id": unit["unit_id"],
                    "verdict": verdict,
                    "feedback": feedback,
                    "source_aliases": [
                        alias
                        for alias in unit["source_aliases"]
                        if alias != "S999"  # Deliberately invalid source in the negative fixture.
                    ],
                }
                for unit in requested_review_units(request)
            ]
        }
    )


class GeneralProvider(ScriptedAgentProvider):
    """Return ordinary prose and small reviewer decisions without any Worker JSON."""

    def __init__(self, *, tool=False, revise=False, malformed=False):
        """Select observable tool, revision or invalid-review behavior."""
        super().__init__()
        self.tool = tool
        self.revise = revise
        self.malformed = malformed
        self.reviews = 0

    async def generate(self, request):
        """Fail if the new loop accidentally forces provider JSON mode or the old graph."""
        self.requests.append(request)
        assert request.output_schema is None and not request.json_object_output
        role = request.metadata["agent_role"]
        if role == "reviewer":
            self.reviews += 1
            text = (
                "not JSON"
                if self.malformed
                else unit_review_text(
                    request,
                    "revise" if self.reviews <= int(self.revise) else "supported",
                    "Explain limitations" if self.revise else "Checked",
                )
            )
        elif self.tool and not any(m.role.value == "tool" for m in request.messages):
            return ModelResponse(
                tool_calls=[ToolCall(id="files-1", name="list_run_files", arguments={})],
                finish_reason="tool_calls",
                input_tokens=10,
                output_tokens=5,
                latency_ms=1,
                raw_response={"text": None},
            )
        else:
            text = "普通回答，不是 JSON。" + ("已说明限制。" if self.reviews else "")
        return ModelResponse(
            text=text,
            finish_reason="stop",
            input_tokens=10,
            output_tokens=5,
            latency_ms=1,
            raw_response={"text": text},
        )


@pytest.mark.parametrize(
    "policy,reviews", [("never", 0), ("always", 1), ("on_verification_failure", 0)]
)
async def test_plain_answer_preserves_review_policy_and_replays(client, policy, reviews):
    """Plain prose completes without Controller planning or Worker JSON, with exact policy."""
    provider = GeneralProvider()
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)
    run = await create_test_run(client)
    payload = general_payload(policy)
    response = await client.post(f"/api/v1/runs/{run['run_id']}/agent-workflows", json=payload)
    assert response.status_code == 201, response.text
    result = response.json()
    assert result["status"] == "completed", result
    assert result["final_output"]["final_text"].startswith("普通回答")
    assert provider.reviews == reviews
    assert not any(n["node_key"] == "controller_planning" for n in result["nodes"])
    count = len(provider.requests)
    replay = await client.post(f"/api/v1/runs/{run['run_id']}/agent-workflows", json=payload)
    assert replay.status_code == 200
    assert len(provider.requests) == count
    restored = await client.get(f"/api/v1/agent-workflows/{result['workflow_execution_id']}/result")
    assert restored.json()["final_output"] == result["final_output"]


async def test_general_agent_continues_tools_and_finishes_plain_text(client):
    """Native tool results reach the next model turn and are counted exactly once."""
    provider = GeneralProvider(tool=True)
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)
    run = await create_test_run(client)
    response = await client.post(
        f"/api/v1/runs/{run['run_id']}/agent-workflows", json=general_payload()
    )
    assert response.status_code == 201, response.text
    result = response.json()
    assert result["status"] == "completed", result
    assert len(provider.requests) == 2
    assert result["aggregate_usage"]["model_call_count"] == 2
    assert result["aggregate_usage"]["tool_call_count"] == 1


@pytest.mark.parametrize("characters", [12000, 40000])
async def test_located_candidate_does_not_reject_previously_allowed_chinese_answer(
    client, characters,
):
    """Save and restore full Unicode prose without legacy 16k-text or 64KiB-node rejection."""
    provider = GeneralProvider()
    original = provider.generate
    answer = "说明" * (characters // 2)

    async def long_answer(request):
        """Return the same valid Unicode answer without making external model requests."""
        response = await original(request)
        response.text = answer
        return response

    provider.generate = long_answer
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)
    run = await create_test_run(client)
    result = (
        await client.post(f"/api/v1/runs/{run['run_id']}/agent-workflows", json=general_payload())
    ).json()
    assert result["status"] == "completed", result.get("error")
    assert result["final_output"]["final_text"] == answer
    restored = (await client.get(
        f"/api/v1/agent-workflows/{result['workflow_execution_id']}/result",
    )).json()
    assert restored["final_output"]["final_text"] == answer
    message = (await client.get(
        f"/api/v1/messages/{result['final_output']['assistant_message_id']}",
    )).json()
    assert message["content_text"] == answer


async def test_review_revision_returns_to_agent_and_is_reviewed_again(client):
    """A revised candidate cannot inherit the prior review and must retain its feedback."""
    provider = GeneralProvider(revise=True)
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)
    run = await create_test_run(client)
    response = await client.post(
        f"/api/v1/runs/{run['run_id']}/agent-workflows", json=general_payload("always")
    )
    assert response.status_code == 201, response.text
    result = response.json()
    assert result["status"] == "completed", result
    assert provider.reviews == 2 and len(provider.requests) == 4
    assert "已说明限制" in result["final_output"]["final_text"]
    assert result["loop_statistics"]["workflow_round_count"] == 2
    assert result["loop_statistics"]["refinement_count"] == 1
    assert result["loop_statistics"]["refinement_usage"]["model_call_count"] == 2


@pytest.mark.parametrize("repair_second_batch", [False, True])
async def test_review_batches_repair_only_invalid_units_and_keep_all_decisions(
    client, monkeypatch, repair_second_batch
):
    """Repair invalid units per batch, preserving accepted decisions and actual repair counts."""
    from nexuspilot_api.features.agent_runtime.services.general_agent_orchestrator import (
        GeneralAgentOrchestrator,
    )

    def unexpected_failure(self, error):
        """Expose the original regression exception instead of a sanitized workflow envelope."""
        raise error.__cause__ or error

    monkeypatch.setattr(
        GeneralAgentOrchestrator, "_public_error_from_exception", unexpected_failure
    )
    client._transport.raise_app_exceptions = True
    provider = GeneralProvider()
    original = provider.generate
    batches = []

    async def partial_review(request):
        """Corrupt one source per selected batch without discarding its valid peer decisions."""
        response = await original(request)
        if request.metadata["agent_role"] == "reviewer":
            units = requested_review_units(request)
            batches.append([unit["unit_id"] for unit in units])
            if len(batches) == 1 or (repair_second_batch and len(batches) == 3):
                body = json.loads(response.text)
                body["decisions"][1]["source_aliases"] = ["S999"]
                response.text = json.dumps(body)
        else:
            response.text = "\n\n".join(f"Paragraph {index}." for index in range(8))
        return response

    provider.generate = partial_review
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)
    run = await create_test_run(client)
    result = (
        await client.post(
            f"/api/v1/runs/{run['run_id']}/agent-workflows", json=general_payload("always")
        )
    ).json()
    assert result.get("status") == "completed", result
    assert batches == [[f"u{i}" for i in range(1, 7)], ["u2"], ["u7", "u8"]] + (
        [["u8"]] if repair_second_batch else []
    )
    reviews = [node for node in result["nodes"] if node["output_type"] == "general_review"]
    assert len(reviews) == 1
    review = reviews[0]["output"]
    assert review["schema_version"] == "general_review.v2"
    assert [d["unit_id"] for d in review["decisions"]] == [f"u{i}" for i in range(1, 9)]
    assert review["verdict"] == "supported"
    assert result["loop_statistics"]["output_repair_succeeded_count"] == 1 + repair_second_batch
    assert result["aggregate_usage"]["model_call_count"] == 4 + repair_second_batch
    restored = (
        await client.get(f"/api/v1/agent-workflows/{result['workflow_execution_id']}/result")
    ).json()
    assert [n["output"] for n in restored["nodes"] if n["output_type"] == "general_review"] == [
        review
    ]


async def test_invalid_review_is_not_silently_accepted(client):
    """One local review-format repair is allowed; persistent invalid decisions fail honestly."""
    provider = GeneralProvider(malformed=True)
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)
    run = await create_test_run(client)
    response = await client.post(
        f"/api/v1/runs/{run['run_id']}/agent-workflows", json=general_payload("always")
    )
    assert response.status_code == 201, response.text
    result = response.json()
    assert result["status"] == "failed" and result["final_output"] is None
    assert provider.reviews == 2
    assert result["loop_statistics"]["output_repair_requested_count"] == 1
    assert result["loop_statistics"]["output_repair_failed_count"] == 1
    assert result["aggregate_usage"]["model_call_count"] == 3


class FileReadingProvider(GeneralProvider):
    """Read one block, have the reviewer read a second, then correct using that original."""

    def __init__(self, file_id):
        """Use only the fixture's authorized file and remember review rounds."""
        super().__init__()
        self.file_id = file_id
        self.review_read = False

    async def generate(self, request):
        """Assert source provenance reaches review and supplementary reading reaches revision."""
        self.requests.append(request)
        assert request.output_schema is None and not request.json_object_output
        role = request.metadata["agent_role"]
        messages = "\n".join(m.content or "" for m in request.messages)
        tools = [m for m in request.messages if m.role.value == "tool"]
        if role == "reviewer":
            self.reviews += 1
            assert "source_hash" in messages and "parsed_hash" in messages
            assert "ORIGINAL_ALPHA" in messages and '"content_verified":false' in messages
            if not self.review_read:
                self.review_read = True
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
            text = unit_review_text(
                request,
                "revise" if self.reviews == 2 else "supported",
                "Include the supplementary evidence [S2]",
            )
        elif not tools:
            return ModelResponse(
                tool_calls=[
                    ToolCall(
                        id="agent-read",
                        name="read_file_content",
                        arguments={"file_id": self.file_id, "offset": 0, "limit": 1},
                    )
                ],
                finish_reason="tool_calls",
                input_tokens=10,
                output_tokens=5,
                latency_ms=1,
                raw_response={"fixture": "agent-read"},
            )
        else:
            if self.review_read:
                assert "ORIGINAL_BETA" in messages, "Reviewer supplementary original was lost"
                assert '"S2"' in messages
            text = "Alpha [S1]" + (" and Beta [S2]" if self.review_read else "")
        return ModelResponse(
            text=text,
            finish_reason="stop",
            input_tokens=10,
            output_tokens=5,
            latency_ms=1,
            raw_response={"text": text},
        )


async def test_revision_input_keeps_current_evidence_without_accumulating_old_reads(
    client, monkeypatch,
):
    """Three reviews retain stored originals but send only current revision evidence."""
    from test_user_files import reserve_file

    from nexuspilot_api.features.agent_runtime.services.general_agent_orchestrator import (
        GeneralAgentOrchestrator,
    )

    def expose_failure(self, error):
        """Let the provider assertion identify unexpected context accumulation directly."""
        raise error.__cause__ or error

    monkeypatch.setattr(GeneralAgentOrchestrator, "_public_error_from_exception", expose_failure)
    client._transport.raise_app_exceptions = True
    owner = (await client.get("/api/v1/auth/me")).json()["user_id"]
    session = (await client.post("/api/v1/sessions", json={"user_id": owner})).json()
    content = b"BASE_ORIGINAL\nOLD_REVIEW_ORIGINAL\nCURRENT_REVIEW_ORIGINAL\n"
    file = (await reserve_file(client, content)).json()
    assert (await client.put(
        f"/api/v1/files/{file['file_id']}/content", content=content,
    )).status_code == 200
    turn = (await client.post(f"/api/v1/sessions/{session['session_id']}/turns", json={
        "user_id": owner, "content_text": "Explain the attachment", "file_ids": [file["file_id"]],
    })).json()
    provider = GeneralProvider()
    review_round = 0
    revision_sources = []

    async def generate(request):
        """Read distinct reviewer blocks, revise the final citation, then approve that scope."""
        nonlocal review_round
        provider.requests.append(request)
        tools = [m for m in request.messages if m.role.value == "tool"]
        role = request.metadata["agent_role"]
        arguments = None
        if role == "reviewer":
            if not tools:
                review_round += 1
                if review_round < 3:
                    arguments = {"offset": review_round, "limit": 1}
            if arguments is None:
                decision = json.loads(unit_review_text(
                    request, "revise" if review_round < 3 else "supported",
                    f"Review round {review_round}",
                ))
                for item in decision["decisions"]:
                    item["source_aliases"] = [f"S{min(review_round + 1, 3)}"]
                text = json.dumps(decision)
        elif not tools:
            arguments = {"offset": 0, "limit": 1}
        else:
            for message in request.messages:
                try:
                    value = json.loads(message.content or "")
                except (TypeError, ValueError):
                    continue
                if isinstance(value, dict) and "review_sources" in value:
                    revision_sources.append(set(value["review_sources"]))
            if review_round == 2:
                assert revision_sources[-1] == {"S1", "S3"}
            alias = "S3" if review_round == 2 else "S1"
            text = f"Candidate revision {review_round} [{alias}]"
        if arguments is not None:
            return ModelResponse(tool_calls=[ToolCall(
                id=f"read-{role}-{review_round}", name="read_file_content",
                arguments={"file_id": file["file_id"], **arguments},
            )], finish_reason="tool_calls", input_tokens=10, output_tokens=5, latency_ms=1,
                raw_response={})
        return ModelResponse(text=text, finish_reason="stop", input_tokens=10,
                             output_tokens=5, latency_ms=1, raw_response={})

    provider.generate = generate
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)
    response = await client.post(f"/api/v1/runs/{turn['run']['run_id']}/agent-workflows",
                                 json=general_payload("always"))
    assert response.status_code == 201, response.text
    result = response.json()
    assert result["status"] == "completed", response.text
    assert revision_sources == [set(), {"S1", "S2"}, {"S1", "S3"}]
    assert result["loop_statistics"]["refinement_count"] == 2
    restored = (await client.get(
        f"/api/v1/agent-workflows/{result['workflow_execution_id']}/result",
    )).json()
    candidates = [n for n in restored["nodes"] if n["output_type"] == "general_candidate"]
    assert set(candidates[-1]["output"]["sources"]) == {"S1", "S2", "S3"}
    assert candidates[1]["output"]["sources"]["S2"] == candidates[-1]["output"]["sources"]["S2"]


@pytest.mark.parametrize("revoke_before_review", [False, True])
async def test_file_sources_review_reread_and_object_body_restore(
    client,
    test_database_session_factory,
    monkeypatch,
    revoke_before_review,
):
    """Persist reads, restore bodies and continue; revoked files must not reach review."""
    from sqlalchemy import text
    from test_user_files import reserve_file

    from nexuspilot_api.features.agent_runtime.services.general_agent_orchestrator import (
        GeneralAgentOrchestrator,
    )

    def unexpected_failure(self, error):
        """Expose a fixture failure rather than hiding its traceback in a public error."""
        raise error.__cause__ or error

    if not revoke_before_review:
        monkeypatch.setattr(
            GeneralAgentOrchestrator, "_public_error_from_exception", unexpected_failure
        )
        client._transport.raise_app_exceptions = True

    content = b"ORIGINAL_ALPHA\nORIGINAL_BETA\n"
    owner = (await client.get("/api/v1/auth/me")).json()["user_id"]
    session = (await client.post("/api/v1/sessions", json={"user_id": owner})).json()
    file = (await reserve_file(client, content)).json()
    assert (
        await client.put(f"/api/v1/files/{file['file_id']}/content", content=content)
    ).status_code == 200
    turn = (
        await client.post(
            f"/api/v1/sessions/{session['session_id']}/turns",
            json={
                "user_id": owner,
                "content_text": "Compare these two lines",
                "file_ids": [file["file_id"]],
            },
        )
    ).json()
    provider = FileReadingProvider(file["file_id"])
    if revoke_before_review:
        from nexuspilot_api.features.agent_runtime.services import (
            workflow_execution_state_service,
        )
        from nexuspilot_api.models import UserFile

        state_service = workflow_execution_state_service.WorkflowExecutionStateService
        complete_node = state_service.complete_node

        async def revoke_after_candidate(service, workflow, node, **kwargs):
            """Revoke the previously read source after candidate commit but before review starts."""
            await complete_node(service, workflow, node, **kwargs)
            if node.output_type == "general_candidate":
                record = await service.db_session.get(UserFile, file["file_id"])
                record.status = "deleted"
                await service.db_session.commit()

        monkeypatch.setattr(state_service, "complete_node", revoke_after_candidate)
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)
    response = await client.post(
        f"/api/v1/runs/{turn['run']['run_id']}/agent-workflows", json=general_payload("always")
    )
    assert response.status_code == 201, response.text
    result = response.json()
    if revoke_before_review:
        assert result["status"] == "failed" and result["final_output"] is None
        assert provider.reviews == 0, "Revoked original must not be sent to another model"
        return
    assert result["status"] == "completed", response.text
    assert result["execution_profile"] == "general_tools_v1"
    candidates = [n for n in result["nodes"] if n["output_type"] == "general_candidate"]
    assert len(candidates) == 2
    assert set(candidates[-1]["output"]["sources"]) == {"S1", "S2"}
    assert candidates[0]["output"]["schema_version"] == "general_candidate.v2"
    assert candidates[0]["output"]["units"][0]["source_aliases"] == ["S1"]
    assert candidates[-1]["output"]["units"][0]["source_aliases"] == ["S1", "S2"]
    assert candidates[0]["output"]["read_coverage"][0]["unreturned_block_count"] == 1
    assert candidates[-1]["output"]["read_coverage"][0]["all_parsed_blocks_returned"]
    assert not candidates[-1]["output"]["read_coverage"][0]["model_understanding_verified"]
    assert result["aggregate_usage"]["tool_call_count"] == 2
    assert result["aggregate_usage"]["model_call_count"] == 6
    async with test_database_session_factory() as db:
        row = (
            await db.execute(
                text(
                    "SELECT output_json, body_ref_json FROM llm_agent_workflow_node_executions "
                    "WHERE node_execution_id = :id"
                ),
                {"id": candidates[-1]["node_execution_id"]},
            )
        ).one()
        assert row.body_ref_json and "ORIGINAL_ALPHA" not in str(row.output_json)
        from nexuspilot_api.infrastructure.object_storage import get_object_storage
        from nexuspilot_api.models import LlmModelAttempt

        storage = app.dependency_overrides[get_object_storage]()
        attempts = list(await db.scalars(select(LlmModelAttempt).where(
            LlmModelAttempt.run_id == turn["run"]["run_id"],
        )))
        audits = [json.loads(storage.objects[attempt.raw_request_uri][0]) for attempt in attempts]
        assert any(
            source["input_path"] == ["original_sources", "S1"]
            for audit in audits for source in audit["runtime_source_manifest"]["sources"]
        )
        assert any(
            source["input_path"] == ["review_sources", "S2"]
            for audit in audits for source in audit["runtime_source_manifest"]["sources"]
        )
        for request in provider.requests:
            matching = [audit for audit in audits if audit["effective_messages"] == [
                message.model_dump(mode="json") for message in request.messages
            ]]
            assert matching
            assert matching[0]["effective_request"]["options"]["system_instruction"] == (
                request.system_instruction
            )
    restored = (
        await client.get(f"/api/v1/agent-workflows/{result['workflow_execution_id']}/result")
    ).json()
    assert restored["final_output"] == result["final_output"]
    assert [n["output"] for n in restored["nodes"] if n["output_type"] == "general_candidate"] == [
        node["output"] for node in candidates
    ]
    source_path = (
        f"/api/v1/agent-workflows/{result['workflow_execution_id']}/nodes/"
        f"{candidates[-1]['node_execution_id']}/claim-sources?claim_key=u1"
    )
    originals = await client.get(source_path)
    assert originals.status_code == 200, originals.text
    assert {item["read_by"] for item in originals.json()["sources"]} == {"worker", "reviewer"}
    assert "ORIGINAL_ALPHA" in originals.text and "ORIGINAL_BETA" in originals.text
    assert "minio://" not in originals.text and "source_hash" not in originals.text
    assert originals.headers["cache-control"] == "no-store"
    continuation = await client.post(
        f"/api/v1/sessions/{session['session_id']}/turns",
        json={
            "user_id": owner,
            "content_text": "Continue discussing these lines",
        },
    )
    assert continuation.status_code == 201, continuation.text
    from nexuspilot_api.features.identity.resource_access import authorize_resource_request
    from nexuspilot_api.features.identity.schemas.authentication import LocalWorkspacePrincipalRead
    from nexuspilot_api.models import UserFile

    app.dependency_overrides.pop(authorize_resource_request, None)
    assert (await client.get(source_path)).status_code == 200

    async def foreign_principal(*args):
        """Represent another user without granting access to the current candidate's source."""
        return LocalWorkspacePrincipalRead(
            user_id="foreign-source-reader", display_name="Other", scopes=[]
        )

    with monkeypatch.context() as isolated:
        isolated.setattr(
            "nexuspilot_api.features.identity.resource_access.get_workspace_principal",
            foreign_principal,
        )
        denied = await client.get(source_path)
        assert denied.status_code == 404 and "ORIGINAL_ALPHA" not in denied.text
    async with test_database_session_factory() as db:
        stored_file = await db.get(UserFile, file["file_id"])
        stored_file.status = "deleting"
        await db.commit()
    revoked = await client.get(source_path)
    assert revoked.status_code in {404, 409} and "ORIGINAL_ALPHA" not in revoked.text


@pytest.mark.parametrize("policy", ["never", "on_verification_failure", "always"])
async def test_fabricated_source_cannot_be_approved_or_loop_forever(client, policy):
    """Reject fabricated sources, with finite field repair for an inconsistent reviewer approval."""
    provider = GeneralProvider()
    original = provider.generate

    async def fabricate(request):
        """Emit the same fabricated reference to exercise deterministic no-progress handling."""
        response = await original(request)
        if request.metadata["agent_role"] != "reviewer":
            response.text = "Claim [S999]"
        return response

    provider.generate = fabricate
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)
    run = await create_test_run(client)
    result = (
        await client.post(
            f"/api/v1/runs/{run['run_id']}/agent-workflows", json=general_payload(policy)
        )
    ).json()
    assert result["status"] == "failed" and result["final_output"] is None, result
    assert len(provider.requests) == (2 if policy == "never" else 3)
    assert provider.reviews == (0 if policy == "never" else 2)
    assert result["loop_statistics"]["output_repair_count"] == int(policy != "never")


async def test_uncited_unit_triggers_conditional_review_without_forcing_false_citation(client):
    """A missing paragraph reference needs content judgment, not mandatory invented evidence."""
    from test_user_files import reserve_file

    content = b"ORIGINAL_ALPHA\n"
    owner = (await client.get("/api/v1/auth/me")).json()["user_id"]
    session = (await client.post("/api/v1/sessions", json={"user_id": owner})).json()
    file = (await reserve_file(client, content)).json()
    assert (
        await client.put(f"/api/v1/files/{file['file_id']}/content", content=content)
    ).status_code == 200
    turn = (
        await client.post(
            f"/api/v1/sessions/{session['session_id']}/turns",
            json={
                "user_id": owner,
                "content_text": "Explain the material with general advice",
                "file_ids": [file["file_id"]],
            },
        )
    ).json()
    provider = FileReadingProvider(file["file_id"])
    original = provider.generate

    async def uncited_advice(request):
        """The reviewer accepts an uncited general-advice paragraph without inventing a source."""
        if request.metadata["agent_role"] == "reviewer":
            provider.requests.append(request)
            provider.reviews += 1
            text = unit_review_text(
                request, "supported", "File fact supported; general advice needs no file citation"
            )
            return ModelResponse(
                text=text,
                finish_reason="stop",
                input_tokens=10,
                output_tokens=5,
                latency_ms=1,
                raw_response={"text": text},
            )
        response = await original(request)
        if not response.tool_calls:
            response.text += "\n\nGeneral advice about further reading."
        return response

    provider.generate = uncited_advice
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)
    result = (
        await client.post(
            f"/api/v1/runs/{turn['run']['run_id']}/agent-workflows",
            json=general_payload("on_verification_failure"),
        )
    ).json()
    assert result["status"] == "completed", result
    assert provider.reviews == 1
    assert result["loop_statistics"]["refinement_count"] == 0
    review = next(n["output"] for n in result["nodes"] if n["output_type"] == "general_review")
    assert review["decisions"][1]["source_aliases"] == []


@pytest.mark.parametrize(
    "configuration",
    [
        {"workflow_name": "model_only"},
        {"workflow_version": "1.1.0"},
        {"execution_profile": "model_only_v1"},
    ],
)
async def test_general_definition_cannot_be_mixed_with_legacy(client, configuration):
    """Reject ambiguous identities before creating a workflow or invoking a model."""
    run = await create_test_run(client)
    response = await client.post(
        f"/api/v1/runs/{run['run_id']}/agent-workflows", json=general_payload(**configuration)
    )
    assert response.status_code == 422


async def test_general_result_is_not_readable_by_another_user(resource_client, monkeypatch):
    """Authorize general workflow snapshots before opening their object-backed private text."""
    from nexuspilot_api.features.identity.schemas.authentication import LocalWorkspacePrincipalRead
    from nexuspilot_api.infrastructure.object_storage import get_object_storage

    client = resource_client
    owner = (await client.get("/api/v1/auth/me")).json()["user_id"]
    session = (await client.post("/api/v1/sessions", json={"user_id": owner})).json()
    turn = (
        await client.post(
            f"/api/v1/sessions/{session['session_id']}/turns",
            json={"user_id": owner, "content_text": "Private question"},
        )
    ).json()
    provider = GeneralProvider()
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)
    response = await client.post(
        f"/api/v1/runs/{turn['run']['run_id']}/agent-workflows", json=general_payload()
    )
    assert response.status_code == 201, response.text
    result = response.json()
    assert result["status"] == "completed", result

    async def other_principal(*args):
        """Represent a different authenticated workspace user."""
        return LocalWorkspacePrincipalRead(user_id="another-user", display_name="Other", scopes=[])

    async def forbidden_read(*args, **kwargs):
        """An unauthorized result request must not read private object payloads."""
        raise AssertionError("Unauthorized object read")

    monkeypatch.setattr(
        "nexuspilot_api.features.identity.resource_access.get_workspace_principal", other_principal
    )
    monkeypatch.setattr(
        app.dependency_overrides[get_object_storage](), "open_object", forbidden_read
    )
    denied = await client.get(f"/api/v1/agent-workflows/{result['workflow_execution_id']}/result")
    assert denied.status_code == 404, denied.text


@pytest.mark.parametrize("decision", ["unconfirmed", "format_repaired"])
async def test_review_uncertainty_or_format_repair_remains_visible(client, decision):
    """Deliver explicit uncertainty or one valid local repair without inventing review success."""
    provider = GeneralProvider()
    original = provider.generate

    async def review_variant(request):
        """Return uncertainty, or malformed first output followed by a valid small decision."""
        if request.metadata.get("node_output_repair"):
            assert any("普通回答，不是 JSON" in m.content for m in request.messages)
            assert not request.tools
        response = await original(request)
        if request.metadata["agent_role"] == "reviewer":
            response.text = (
                "invalid"
                if decision == "format_repaired" and provider.reviews == 1
                else unit_review_text(
                    request,
                    "unconfirmed" if decision == "unconfirmed" else "supported",
                    "Source cannot establish every claim"
                    if decision == "unconfirmed"
                    else "Checked",
                )
            )
        return response

    provider.generate = review_variant
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)
    run = await create_test_run(client)
    result = (
        await client.post(
            f"/api/v1/runs/{run['run_id']}/agent-workflows", json=general_payload("always")
        )
    ).json()
    assert result["status"] == "completed", result
    if decision == "unconfirmed":
        assert "审核未能确认全部内容" in result["final_output"]["final_text"]
    else:
        assert result["loop_statistics"]["output_repair_succeeded_count"] == 1
        assert result["loop_statistics"]["repair_usage"]["model_call_count"] == 1


async def test_general_refinement_is_not_capped_at_two_legacy_rounds(client):
    """Two distinct corrections may lead to a third candidate without the legacy round cap."""
    provider = GeneralProvider(revise=2)
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)
    run = await create_test_run(client)
    result = (
        await client.post(
            f"/api/v1/runs/{run['run_id']}/agent-workflows", json=general_payload("always")
        )
    ).json()
    assert result["status"] == "completed", result
    assert result["loop_statistics"]["workflow_round_count"] == 3
    assert result["loop_statistics"]["refinement_count"] == 2
    assert result["aggregate_usage"]["model_call_count"] == 6


async def test_node_budget_cannot_publish_an_unfinishable_answer(client):
    """Reserve space for both final persistence nodes before publishing an assistant answer."""
    provider = GeneralProvider()
    original = provider.generate

    async def correct_late(request):
        """Use eight distinct failed candidates, then a valid ninth near the node limit."""
        response = await original(request)
        count = len(provider.requests)
        if count < 9:
            response.text = f"Draft {count} [S999]"
        return response

    provider.generate = correct_late
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)
    run = await create_test_run(client)
    result = (
        await client.post(
            f"/api/v1/runs/{run['run_id']}/agent-workflows",
            json=general_payload(max_nodes=10),
        )
    ).json()
    assert result["status"] == "failed"
    assert result["final_output"] is None
    assert result["aggregate_usage"]["model_call_count"] == 9
