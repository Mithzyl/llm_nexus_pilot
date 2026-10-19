"""Exercise actual delegated tasks and immutable handoff results through the general loop."""

import asyncio
import copy
import json

import pytest
from nexuspilot_models.contracts import ModelResponse, ToolCall
from sqlalchemy import select
from test_agent_workflows import register_default_agent_test_model, scripted_registry  # noqa: F401
from test_api import create_test_run
from test_conversation_file_context import prepare_conversation_file
from test_general_agent import (  # noqa: F401
    GeneralProvider,
    general_model_catalog,
    general_payload,
    unit_review_text,
)

from nexuspilot_api.core.dependencies import get_provider_registry
from nexuspilot_api.core.errors import ResourceConflictError
from nexuspilot_api.features.memory.schemas.collaboration_memory import AgentHandoffCreate
from nexuspilot_api.features.memory.services.collaboration_memory_service import submit_handoff
from nexuspilot_api.infrastructure.object_storage import get_object_storage
from nexuspilot_api.main import app
from nexuspilot_api.models import LlmAgentHandoff, LlmAgentRun, LlmTask


class DelegatingProvider(GeneralProvider):
    """Have the controller request one real planner and consume its prose handoff."""

    async def generate(self, request):
        """Assert child tool scope and return prose, never a model-authored platform record."""
        self.requests.append(request)
        role = request.metadata["agent_role"]
        tools = {tool.name for tool in request.tools}
        if role == "planner":
            assert "delegate_task" not in tools
            assert any("Compare two approaches" in m.content for m in request.messages)
            return ModelResponse(
                text="Delegated analysis: compare complexity and maintainability.",
                finish_reason="stop",
                input_tokens=10,
                output_tokens=5,
                latency_ms=1,
                raw_response={},
            )
        assert "delegate_task" in tools
        results = [m for m in request.messages if m.role.value == "tool"]
        if not results:
            return ModelResponse(
                tool_calls=[
                    ToolCall(
                        id="delegate-1",
                        name="delegate_task",
                        arguments={"role": "planner", "objective": "Compare two approaches"},
                    )
                ],
                finish_reason="tool_calls",
                input_tokens=10,
                output_tokens=5,
                latency_ms=1,
                raw_response={},
            )
        result = json.loads(results[-1].content)
        assert result["status"] == "completed", result
        assert result["result"]["candidate"]["text"].startswith("Delegated analysis:")
        assert result["result"]["handoff_id"]
        return ModelResponse(
            text="Compare complexity and maintainability before choosing.",
            finish_reason="stop",
            input_tokens=10,
            output_tokens=5,
            latency_ms=1,
            raw_response={},
        )


@pytest.mark.parametrize("extra_text", ["", "A" * 40000], ids=["normal", "large-prose"])
async def test_general_agent_delegates_and_records_real_task_agent_and_handoff(
    client,
    test_database_session_factory,
    monkeypatch,
    extra_text,
):
    """Persist full child prose under inherited deadlines without double-counting model usage."""
    run = await create_test_run(client)
    provider = DelegatingProvider()
    original_generate = provider.generate
    timeout_values = []
    original_timeout = asyncio.timeout

    def capture_timeout(seconds):
        """Observe actual runtime deadlines rather than only checking catalog metadata."""
        timeout_values.append(seconds)
        return original_timeout(seconds)

    async def generate_with_extra_prose(request):
        """Exercise child prose larger than a file-page envelope without truncation."""
        response = await original_generate(request)
        if request.metadata["agent_role"] == "planner":
            response.text += extra_text
        return response

    monkeypatch.setattr(asyncio, "timeout", capture_timeout)
    provider.generate = generate_with_extra_prose
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)
    payload = general_payload("never")
    payload["role_bindings"]["planner"] = dict(payload["role_bindings"]["controller"])
    payload["role_bindings"]["planner"]["max_output_tokens"] = 42
    response = await client.post(f"/api/v1/runs/{run['run_id']}/agent-workflows", json=payload)
    assert response.status_code == 201, response.text
    result = response.json()
    assert result["status"] == "completed", (
        [(t["tool_name"], t["status"], t["error_code"])
         for n in result["nodes"] for t in n["evidence"]["tool_results"]],
    )
    assert result["aggregate_usage"]["model_call_count"] == 3
    assert result["aggregate_usage"]["input_tokens"] == 30
    assert result["final_output"]["final_text"].startswith("Compare complexity")
    assert len(result["final_output"]["source_handoff_ids"]) == 1
    assert provider.requests[1].max_output_tokens == 42
    assert None in timeout_values
    assert 15 not in timeout_values
    async with test_database_session_factory() as db:
        tasks = list(await db.scalars(select(LlmTask).where(LlmTask.run_id == run["run_id"])))
        parent = next(task for task in tasks if task.assigned_role == "controller")
        child = next(task for task in tasks if task.assigned_role == "planner")
        assert child.parent_task_id == parent.task_id
        assert child.objective == "Compare two approaches"
        assert child.status.value == "completed"
        agent = await db.scalar(select(LlmAgentRun).where(LlmAgentRun.task_id == child.task_id))
        assert agent.status.value == "completed"
        handoff = await db.scalar(
            select(LlmAgentHandoff).where(LlmAgentHandoff.task_id == child.task_id)
        )
        assert handoff.agent_run_id == agent.agent_run_id
        assert handoff.schema_version == "agent_handoff.v3"
        assert handoff.handoff_json["candidate"]["text"].startswith("Delegated analysis:")
        if extra_text:
            assert handoff.handoff_json["candidate"]["text"].endswith(extra_text)
        forged = copy.deepcopy(handoff.handoff_json)
        forged["candidate"]["text"] = "Invented work"
        with pytest.raises((ResourceConflictError, ValueError)):
            await submit_handoff(
                db,
                app.dependency_overrides[get_object_storage](),
                run["run_id"],
                AgentHandoffCreate(
                    agent_run_id=agent.agent_run_id,
                    task_id=child.task_id,
                    schema_version="agent_handoff.v3",
                    handoff_json=forged,
                    idempotency_key="forged-delegation",
                ),
            )


async def test_child_reference_revision_is_recorded_without_inventing_main_rounds(
    client, test_database_session_factory,
):
    """Record child revisions separately and include their real model usage once."""
    from nexuspilot_api.models import LlmAgentWorkflowEvent

    _, _, file, turn = await prepare_conversation_file(client)
    provider = FileDelegatingProvider(file["file_id"], repair_reference=True)
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)
    payload = general_payload("never")
    payload["role_bindings"]["planner"] = dict(payload["role_bindings"]["controller"])
    response = await client.post(
        f"/api/v1/runs/{turn['run']['run_id']}/agent-workflows", json=payload,
    )
    result = response.json()
    assert result["status"] == "completed", result
    assert result["aggregate_usage"]["model_call_count"] == 5
    assert result["loop_statistics"]["workflow_round_count"] == 1
    assert result["loop_statistics"]["refinement_count"] == 0
    async with test_database_session_factory() as db:
        events = list(await db.scalars(select(LlmAgentWorkflowEvent).where(
            LlmAgentWorkflowEvent.run_id == turn["run"]["run_id"],
            LlmAgentWorkflowEvent.event_type == "agent.delegation.revision.requested",
        )))
        assert len(events) == 1
        assert events[0].public_payload_json["revision_index"] == 1


async def test_handoff_failure_does_not_mark_child_task_completed(
    client, test_database_session_factory, monkeypatch,
):
    """A produced child answer is not a completed task until its handoff is durable."""
    from nexuspilot_api.features.agent_runtime.services import general_delegation_service

    async def fail_handoff(*args, **kwargs):
        """Simulate object-store submission failure after the child model has finished."""
        raise RuntimeError("Synthetic handoff storage failure")

    monkeypatch.setattr(general_delegation_service, "submit_handoff", fail_handoff)
    run = await create_test_run(client)
    provider = DelegatingProvider()
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)
    payload = general_payload("never")
    payload["role_bindings"]["planner"] = dict(payload["role_bindings"]["controller"])
    response = await client.post(f"/api/v1/runs/{run['run_id']}/agent-workflows", json=payload)
    assert response.json()["status"] == "failed", response.text
    assert response.json()["final_output"] is None
    assert len(provider.requests) == 2
    async with test_database_session_factory() as db:
        child = await db.scalar(select(LlmTask).where(
            LlmTask.run_id == run["run_id"], LlmTask.assigned_role == "planner",
        ))
        assert child.status.value == "failed"


class FileDelegatingProvider(GeneralProvider):
    """Read in the child and require original blocks, not just a summary, in the parent."""

    def __init__(self, file_id, *, repair_reference=False):
        """Pin the attachment and optionally require one child reference correction."""
        super().__init__()
        self.file_id = file_id
        self.repair_reference = repair_reference
        self.child_answers = 0

    async def generate(self, request):
        """Exercise child reads, parent originals and independent review under the fixed policy."""
        self.requests.append(request)
        role = request.metadata["agent_role"]
        previous = [m for m in request.messages if m.role.value == "tool"]
        call = None
        if role == "planner":
            assert "delegate_task" not in {t.name for t in request.tools}
            assert not any(m.tool_name == "delegate_task" for m in previous)
            if not previous:
                call = ToolCall(
                    id="child-read",
                    name="read_file_content",
                    arguments={"file_id": self.file_id, "limit": 1},
                )
            answer = "Delegated original is UNIQUE_FILE_TEXT_2718. [S1]"
            if previous:
                self.child_answers += 1
                if self.repair_reference and self.child_answers == 1:
                    answer = "Delegated original is UNIQUE_FILE_TEXT_2718. [S999]"
        elif role == "reviewer":
            self.reviews += 1
            assert "UNIQUE_FILE_TEXT_2718" in request.messages[-1].content
            answer = unit_review_text(request, "supported", "Checked against original")
        elif not previous:
            call = ToolCall(
                id="delegate-file",
                name="delegate_task",
                arguments={"role": "planner", "objective": "Read the supplied file"},
            )
            answer = ""
        else:
            body = json.loads(previous[-1].content)["result"]
            assert body["content_verified"] is False
            assert body["revision_count"] == int(self.repair_reference)
            assert body["original_sources"]["S1"]["blocks"][0]["text"] == "UNIQUE_FILE_TEXT_2718"
            answer = "The original says UNIQUE_FILE_TEXT_2718. [S1]"
        return ModelResponse(
            text=answer if call is None else "",
            tool_calls=[call] if call else [],
            finish_reason="tool_calls" if call else "stop",
            input_tokens=10,
            output_tokens=5,
            latency_ms=1,
            raw_response={},
        )


@pytest.mark.parametrize(
    "policy,reviews", [("never", 0), ("always", 1), ("on_verification_failure", 0)]
)
async def test_delegated_originals_reach_parent_review_and_source_query(client, policy, reviews):
    """Keep actual child provenance available through Handoff, final candidate and source reads."""
    _, _, file, turn = await prepare_conversation_file(client)
    provider = FileDelegatingProvider(file["file_id"])
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)
    payload = general_payload(policy)
    payload["role_bindings"]["planner"] = dict(payload["role_bindings"]["controller"])
    response = await client.post(
        f"/api/v1/runs/{turn['run']['run_id']}/agent-workflows",
        json=payload,
    )
    assert response.status_code == 201, response.text
    result = response.json()
    assert result["status"] == "completed", result
    assert provider.reviews == reviews
    assert result["aggregate_usage"]["model_call_count"] == 4 + reviews
    main = next(n for n in result["nodes"] if n["output_type"] == "general_candidate")
    source = await client.get(
        f"/api/v1/agent-workflows/{result['workflow_execution_id']}/nodes/"
        f"{main['node_execution_id']}/claim-sources?claim_key=u1"
    )
    assert source.status_code == 200, source.text
    assert "UNIQUE_FILE_TEXT_2718" in source.text


@pytest.mark.parametrize(
    "arguments,error",
    [
        ({"role": "researcher", "objective": "Unconfigured role"}, "delegated_role_not_configured"),
        (
            {"role": "planner", "objective": "Forged source", "source_aliases": ["S999"]},
            "unknown_delegated_source",
        ),
        (
            {"role": "planner", "objective": "Expand access", "user_id": "forged"},
            "invalid_arguments",
        ),
    ],
)
async def test_invalid_delegation_does_not_start_child(
    client, test_database_session_factory, arguments, error
):
    """Return a recognizable tool failure before creating a child or charging another role."""

    class DeniedProvider(GeneralProvider):
        """Ask for invalid delegation and respond safely to the tool's denial."""

        async def generate(self, request):
            """Fail the test if an unauthorized child request reaches the provider."""
            self.requests.append(request)
            assert request.metadata["agent_role"] == "controller"
            previous = [m for m in request.messages if m.role.value == "tool"]
            if previous:
                result = json.loads(previous[-1].content)
                assert result["status"] in {"failed", "permission_denied"}
                assert result["error_code"] == error
                return ModelResponse(
                    text="Cannot delegate this request.",
                    finish_reason="stop",
                    input_tokens=1,
                    output_tokens=1,
                    latency_ms=1,
                    raw_response={},
                )
            return ModelResponse(
                tool_calls=[
                    ToolCall(id="invalid-delegate", name="delegate_task", arguments=arguments)
                ],
                finish_reason="tool_calls",
                input_tokens=1,
                output_tokens=1,
                latency_ms=1,
                raw_response={},
            )

    run = await create_test_run(client)
    provider = DeniedProvider()
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)
    payload = general_payload("never")
    payload["role_bindings"]["planner"] = dict(payload["role_bindings"]["controller"])
    response = await client.post(f"/api/v1/runs/{run['run_id']}/agent-workflows", json=payload)
    assert response.status_code == 201
    assert response.json()["status"] == "completed", response.text
    assert len(provider.requests) == 2
    async with test_database_session_factory() as db:
        assert (
            len(
                list(
                    await db.scalars(
                        select(LlmTask).where(
                            LlmTask.run_id == run["run_id"],
                        )
                    )
                )
            )
            == 1
        )
        assert not list(
            await db.scalars(
                select(LlmAgentHandoff).where(
                    LlmAgentHandoff.run_id == run["run_id"],
                )
            )
        )
