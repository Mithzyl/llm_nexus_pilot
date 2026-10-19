"""Exercise the opt-in general loop through API persistence without paid model requests."""

import json

import pytest
from nexuspilot_models.contracts import ModelResponse, ToolCall
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
                else json.dumps(
                    {
                        "verdict": "revise" if self.reviews <= int(self.revise) else "supported",
                        "feedback": "Explain limitations" if self.revise else "Checked",
                    }
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
            text = json.dumps(
                {
                    "verdict": "revise" if self.reviews == 2 else "supported",
                    "feedback": "Include the supplementary evidence [S2]",
                }
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
    restored = (
        await client.get(f"/api/v1/agent-workflows/{result['workflow_execution_id']}/result")
    ).json()
    assert restored["final_output"] == result["final_output"]
    continuation = await client.post(
        f"/api/v1/sessions/{session['session_id']}/turns",
        json={
            "user_id": owner,
            "content_text": "Continue discussing these lines",
        },
    )
    assert continuation.status_code == 201, continuation.text


@pytest.mark.parametrize("policy", ["never", "on_verification_failure", "always"])
async def test_fabricated_source_cannot_be_approved_or_loop_forever(client, policy):
    """Backend rejects unissued source aliases even if an independent reviewer approves them."""
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
    assert len(provider.requests) == (2 if policy == "never" else 4)
    assert provider.reviews == (0 if policy == "never" else 2)


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
                else json.dumps(
                    {
                        "verdict": "unconfirmed" if decision == "unconfirmed" else "supported",
                        "feedback": "Source cannot establish every claim"
                        if decision == "unconfirmed"
                        else "Checked",
                    }
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
