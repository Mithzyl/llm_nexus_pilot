"""Fault-inject Worker loss at durable tool-loop boundaries without paid providers."""

from datetime import timedelta

import pytest
from conftest import fake_price_catalog
from nexuspilot_models.contracts import ProviderContinuationKind, ProviderContinuationState
from sqlalchemy import select
from test_agent_workflows import register_default_agent_test_model, scripted_registry  # noqa: F401
from test_api import create_test_run
from test_general_agent import GeneralProvider, general_model_catalog, general_payload  # noqa: F401

from nexuspilot_api.core.config import get_settings
from nexuspilot_api.features.agent_runtime.services.workflow_ownership import claim_workflow
from nexuspilot_api.features.agent_runtime.services.workflow_worker import execute_claim
from nexuspilot_api.models import LlmAgentWorkflowExecution, LlmModelAttempt, LlmModelToolCall
from nexuspilot_api.models.base import utc_now


@pytest.mark.parametrize(
    "phase",
    [
        "model_response",
        "tool_result",
        "model_commit",
        "tool_commit",
        "review_response",
        "review_commit",
    ],
)
@pytest.mark.parametrize("policy", ["never", "always", "on_verification_failure"])
async def test_worker_resumes_tool_loop_without_repeating_calls(
    client,
    monkeypatch,
    test_database_session_factory,
    phase,
    policy,
):
    """A new owner consumes saved dialogue and keeps exactly one tool execution after a crash."""
    from nexuspilot_api.features.agent_runtime.services import tool_loop_checkpoint

    class ProcessLost(BaseException):
        """Simulate process loss without invoking application exception finalization."""

    original = tool_loop_checkpoint.save_loop_checkpoint
    stopped = False
    if phase.startswith("review_") and policy != "always":
        pytest.skip("This fixed candidate only requests review under always policy")

    async def crash_after_save(*args, **kwargs):
        """Stop once after publishing the requested checkpoint to object storage and SQL."""
        nonlocal stopped
        if phase.startswith("review_"):
            if (
                args[3].node_key == "general_review"
                and kwargs["phase"] == "model_response"
                and not stopped
            ):
                stopped = True
                if phase == "review_response":
                    await original(*args, **kwargs)
                raise ProcessLost()
            return await original(*args, **kwargs)
        if not stopped and (
            (phase == "model_commit" and kwargs["phase"] == "model_response")
            or (phase == "tool_commit" and kwargs["phase"] == "tool_result")
        ):
            stopped = True
            raise ProcessLost()
        result = await original(*args, **kwargs)
        if kwargs["phase"] == phase and not stopped:
            stopped = True
            raise ProcessLost()
        return result

    monkeypatch.setattr(get_settings(), "workflow_execution_backend", "worker")
    run = await create_test_run(client)
    response = await client.post(
        f"/api/v1/runs/{run['run_id']}/agent-workflows", json=general_payload(policy)
    )
    workflow_id = response.json()["workflow_execution_id"]

    class ContinuingProvider(GeneralProvider):
        """Require each resumed tool message to retain its private continuation association."""

        async def generate(self, request):
            """Produce one opaque continuation and verify it reaches the next model turn."""
            if request.metadata["agent_role"] == "controller" and any(
                message.role.value == "tool" for message in request.messages
            ):
                assistants = [message for message in request.messages if message.tool_calls]
                assert assistants[-1]._provider_continuation_state is not None
            response = await super().generate(request)
            if response.tool_calls:
                response.provider_continuation_state = ProviderContinuationState(
                    kind=ProviderContinuationKind.OPENAI_ENCRYPTED_ITEMS,
                    encrypted_reasoning_items=[
                        {"type": "reasoning", "encrypted_content": "test-private"}
                    ],
                )
            return response

    provider = ContinuingProvider(tool=True)
    storage = test_database_session_factory.kw["info"]["body_storage"]
    first = await claim_workflow(test_database_session_factory, workflow_id, "before-crash")
    with monkeypatch.context() as patch:
        patch.setattr(tool_loop_checkpoint, "save_loop_checkpoint", crash_after_save)
        with pytest.raises(ProcessLost):
            await execute_claim(
                test_database_session_factory,
                storage,
                scripted_registry(provider),
                fake_price_catalog(),
                first,
            )
    async with test_database_session_factory() as db:
        workflow = await db.get(LlmAgentWorkflowExecution, workflow_id)
        workflow.execution_lease_expires_at = utc_now() - timedelta(seconds=1)
        await db.commit()
    second = await claim_workflow(test_database_session_factory, workflow_id, "after-crash")
    await execute_claim(
        test_database_session_factory,
        storage,
        scripted_registry(provider),
        fake_price_catalog(),
        second,
    )
    result = (await client.get(f"/api/v1/agent-workflows/{workflow_id}/result")).json()
    assert result["status"] == "completed", result.get("error")
    assert result["final_output"]["final_text"]
    assert len(provider.requests) == (3 if policy == "always" else 2)
    async with test_database_session_factory() as db:
        tools = list(
            await db.scalars(
                select(LlmModelToolCall)
                .join(LlmModelAttempt)
                .where(
                    LlmModelAttempt.run_id == run["run_id"],
                )
            )
        )
        assert len(tools) == 1 and tools[0].status == "completed"


@pytest.mark.parametrize("boundary", ["general_agent", "general_review", "revision"])
async def test_restored_file_sources_and_review_feedback_survive_owner_change(
    client,
    monkeypatch,
    test_database_session_factory,
    boundary,
):
    """Preserve S1/S2 originals and review feedback across main/read/revision interruption."""
    from test_general_agent import FileReadingProvider
    from test_user_files import reserve_file

    from nexuspilot_api.features.agent_runtime.services import tool_loop_checkpoint

    class ProcessLost(BaseException):
        """Stop execution without marking the Workflow as a business failure."""

    original = tool_loop_checkpoint.save_loop_checkpoint
    stopped = False

    async def crash_at_boundary(*args, **kwargs):
        """Interrupt after the selected source or revision state has been committed."""
        nonlocal stopped
        result = await original(*args, **kwargs)
        selected = (args[3].node_key == boundary and kwargs["phase"] == "tool_result") or (
            boundary == "revision"
            and args[5]["workflow_round"] == 2
            and kwargs["phase"] == "before_model"
        )
        if selected and not stopped:
            stopped = True
            raise ProcessLost()
        return result

    monkeypatch.setattr(get_settings(), "workflow_execution_backend", "worker")
    owner = (await client.get("/api/v1/auth/me")).json()["user_id"]
    session = (await client.post("/api/v1/sessions", json={"user_id": owner})).json()
    content = b"ORIGINAL_ALPHA\nORIGINAL_BETA\n"
    file = (await reserve_file(client, content)).json()
    assert (
        await client.put(f"/api/v1/files/{file['file_id']}/content", content=content)
    ).status_code == 200
    turn = (
        await client.post(
            f"/api/v1/sessions/{session['session_id']}/turns",
            json={
                "user_id": owner,
                "content_text": "Compare both lines",
                "file_ids": [file["file_id"]],
            },
        )
    ).json()
    run_id = turn["run"]["run_id"]
    response = await client.post(
        f"/api/v1/runs/{run_id}/agent-workflows", json=general_payload("always")
    )
    workflow_id = response.json()["workflow_execution_id"]
    provider = FileReadingProvider(file["file_id"])
    storage = test_database_session_factory.kw["info"]["body_storage"]
    first = await claim_workflow(test_database_session_factory, workflow_id, "source-before")
    with monkeypatch.context() as patch:
        patch.setattr(tool_loop_checkpoint, "save_loop_checkpoint", crash_at_boundary)
        with pytest.raises(ProcessLost):
            await execute_claim(
                test_database_session_factory,
                storage,
                scripted_registry(provider),
                fake_price_catalog(),
                first,
            )
    async with test_database_session_factory() as db:
        workflow = await db.get(LlmAgentWorkflowExecution, workflow_id)
        workflow.execution_lease_expires_at = utc_now() - timedelta(seconds=1)
        await db.commit()
    second = await claim_workflow(test_database_session_factory, workflow_id, "source-after")
    await execute_claim(
        test_database_session_factory,
        storage,
        scripted_registry(provider),
        fake_price_catalog(),
        second,
    )
    result = (await client.get(f"/api/v1/agent-workflows/{workflow_id}/result")).json()
    assert result["status"] == "completed", result.get("error")
    assert "[S1]" in result["final_output"]["final_text"]
    assert "[S2]" in result["final_output"]["final_text"]
    assert result["aggregate_usage"]["tool_call_count"] == 2
    assert result["aggregate_usage"]["model_call_count"] == 6
    assert len(provider.requests) == 6
    assert result["loop_statistics"]["refinement_count"] == 1


async def test_review_second_native_batch_recovers_prior_decisions(
    client,
    monkeypatch,
    test_database_session_factory,
):
    """Resume a committed second submit_review batch without resubmitting either batch."""
    from test_review_submission import SubmittingReviewer

    from nexuspilot_api.features.agent_runtime.services import tool_loop_checkpoint

    class ProcessLost(BaseException):
        """Stop after a native review result is fully durable."""

    class BatchProvider(SubmittingReviewer):
        """Generate enough candidate paragraphs to require two independent review batches."""

        async def generate(self, request):
            """Keep native review output while returning eight distinct candidate paragraphs."""
            response = await super().generate(request)
            if request.metadata["agent_role"] == "controller":
                response.text = "\n\n".join(f"Paragraph {index}." for index in range(8))
            return response

    original = tool_loop_checkpoint.save_loop_checkpoint

    async def crash_second_batch(*args, **kwargs):
        """Crash only after the second batch's submission is committed, not during model work."""
        await original(*args, **kwargs)
        if kwargs["phase"] == "tool_result" and args[5].get("review_offset", 0) > 0:
            raise ProcessLost()

    monkeypatch.setattr(get_settings(), "workflow_execution_backend", "worker")
    run = await create_test_run(client)
    accepted = await client.post(
        f"/api/v1/runs/{run['run_id']}/agent-workflows", json=general_payload("always")
    )
    identity = accepted.json()["workflow_execution_id"]
    provider = BatchProvider(multi_batch=True)
    storage = test_database_session_factory.kw["info"]["body_storage"]
    first = await claim_workflow(test_database_session_factory, identity, "review-before")
    with monkeypatch.context() as patch:
        patch.setattr(tool_loop_checkpoint, "save_loop_checkpoint", crash_second_batch)
        with pytest.raises(ProcessLost):
            await execute_claim(
                test_database_session_factory,
                storage,
                scripted_registry(provider),
                fake_price_catalog(),
                first,
            )
    async with test_database_session_factory() as db:
        workflow = await db.get(LlmAgentWorkflowExecution, identity)
        workflow.execution_lease_expires_at = utc_now() - timedelta(seconds=1)
        await db.commit()
    second = await claim_workflow(test_database_session_factory, identity, "review-after")
    await execute_claim(
        test_database_session_factory,
        storage,
        scripted_registry(provider),
        fake_price_catalog(),
        second,
    )
    result = (await client.get(f"/api/v1/agent-workflows/{identity}/result")).json()
    assert result["status"] == "completed", result.get("error")
    review = next(
        node["output"] for node in result["nodes"] if node["node_key"] == "general_review"
    )
    assert len(review["decisions"]) == 8
    assert len(provider.requests) == 3
    assert result["aggregate_usage"]["tool_call_count"] == 2
