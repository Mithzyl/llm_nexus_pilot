"""Check browser source projections using stored reads, never client-provided citations."""

import pytest
from sqlalchemy import select
from test_agent_workflows import register_default_agent_test_model  # noqa: F401
from test_worker_evidence import evidence_read  # noqa: F401

from nexuspilot_api.models import LlmAgentWorkflowNodeExecution, UserFile


@pytest.fixture
async def cited_worker(evidence_read, test_database_session_factory):  # noqa: F811
    """Attach one source-backed claim to the real persisted fixture Worker result."""
    run_id, task_id, reference, file_id = evidence_read
    async with test_database_session_factory() as db:
        node = await db.scalar(
            select(LlmAgentWorkflowNodeExecution).where(
                LlmAgentWorkflowNodeExecution.task_id == task_id,
                LlmAgentWorkflowNodeExecution.output_type == "agent_model_execution",
            )
        )
        node.output_json = {
            **node.output_json,
            "structured_output": {
                **node.output_json["structured_output"],
                "claims": [
                    {
                        "claim_key": "introduction",
                        "text": "Report introduction",
                        "evidence_refs": [reference],
                    }
                ],
            },
        }
        await db.commit()
        return (
            f"/api/v1/agent-workflows/{node.workflow_execution_id}/nodes/"
            f"{node.node_execution_id}/claim-sources"
        ), file_id


async def test_claim_source_returns_only_safe_original_projection(client, cited_worker):
    """Return actual source text/range, never storage locations or private model payloads."""
    path, _ = cited_worker
    response = await client.get(path, params={"claim_key": "introduction"})
    assert response.status_code == 200, response.text
    source = response.json()["sources"][0]
    assert source["blocks"][0]["text"] == "Report introduction\n"
    assert source["has_more"] is True
    assert source["read_by"] == "worker"
    assert "source_hash" not in response.text
    assert "minio://" not in response.text
    assert response.headers["cache-control"] == "no-store"


async def test_reviewer_reused_worker_reference_is_not_a_supplemental_read(
    client, cited_worker, test_database_session_factory
):
    """Keep repeated reviewer citations attributed to the actual Worker tool call."""
    path, _ = cited_worker
    async with test_database_session_factory() as db:
        worker = await db.get(LlmAgentWorkflowNodeExecution, path.split("/")[6])
        reviewer = await db.scalar(
            select(LlmAgentWorkflowNodeExecution).where(
                LlmAgentWorkflowNodeExecution.workflow_execution_id == worker.workflow_execution_id,
                LlmAgentWorkflowNodeExecution.output_type == "independent_review",
            )
        )
        reviewer.output_json = {
            **reviewer.output_json,
            "claim_assessments": [
                {
                    "task_id": worker.task_id,
                    "claim_key": "introduction",
                    "verdict": "supported",
                    "explanation": "The original supports this statement",
                    "evidence_refs": worker.output_json["structured_output"]["claims"][0][
                        "evidence_refs"
                    ],
                }
            ],
        }
        await db.commit()
    response = await client.get(path, params={"claim_key": "introduction"})
    assert response.status_code == 200, response.text
    assert [source["read_by"] for source in response.json()["sources"]] == ["worker"]


@pytest.mark.parametrize("condition", ["missing_claim", "deleted", "snapshot", "wrong_workflow"])
async def test_claim_source_rejects_unavailable_or_mismatched_sources(
    client, cited_worker, test_database_session_factory, condition
):
    """Do not return stale snippets or allow a node to be read through another workflow."""
    path, file_id = cited_worker
    async with test_database_session_factory() as db:
        file = await db.get(UserFile, file_id)
        if condition == "deleted":
            file.status = "deleting"
        if condition == "snapshot":
            file.parsed_hash = "0" * 64
        await db.commit()
    if condition == "wrong_workflow":
        parts = path.split("/")
        parts[4] = "00000000-0000-0000-0000-000000000000"
        path = "/".join(parts)
    response = await client.get(
        path,
        params={
            "claim_key": "missing" if condition == "missing_claim" else "introduction",
        },
    )
    assert response.status_code in {404, 409}, response.text
    assert "Report introduction" not in response.text


async def test_claim_source_checks_real_user_identity(
    client, cited_worker, test_database_session_factory, monkeypatch
):
    """Allow the actual local owner, deny anonymous and a different password-login identity."""
    from test_authentication import login, provision_user

    from nexuspilot_api.core.config import get_settings
    from nexuspilot_api.features.identity.resource_access import authorize_resource_request
    from nexuspilot_api.main import app

    path, _ = cited_worker
    app.dependency_overrides.pop(authorize_resource_request, None)
    assert (await client.get(path, params={"claim_key": "introduction"})).status_code == 200
    monkeypatch.setenv("NEXUSPILOT_AUTHENTICATION_ENABLED", "true")
    get_settings.cache_clear()
    try:
        assert (await client.get(path, params={"claim_key": "introduction"})).status_code == 401
        await provision_user(test_database_session_factory, user_id="source-other")
        assert (await login(client)).status_code == 200
        denied = await client.get(path, params={"claim_key": "introduction"})
        assert denied.status_code == 404
        assert "Report introduction" not in denied.text
    finally:
        get_settings.cache_clear()
