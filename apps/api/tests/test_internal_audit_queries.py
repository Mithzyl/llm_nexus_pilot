"""Internal authentication, audit pagination, ownership, and redaction tests."""

from dataclasses import dataclass

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from test_api import create_test_run

from nexuspilot_api.core.audit_redaction import redact_audit_json
from nexuspilot_api.core.config import Settings
from nexuspilot_api.models import (
    LlmModelToolCall,
    LlmOutboxEvent,
    LlmTaskEvaluation,
)

INTERNAL_HEADERS = {"X-Internal-API-Key": "test-internal-key-long-enough"}


@dataclass(frozen=True)
class InternalAuditSeed:
    """Identify related records created for one internal audit test scope."""

    run_id: str
    task_id: str
    model_attempt_id: str
    model_tool_call_ids: list[str]
    task_evaluation_ids: list[str]
    outbox_event_ids: list[str]


def test_settings_reject_shared_public_and_internal_keys() -> None:
    """Verify configuration cannot collapse the two API trust boundaries."""

    with pytest.raises(ValueError, match="internal_api_key must differ from api_key"):
        Settings(
            api_key="same-key-long-enough",
            internal_api_key="same-key-long-enough",
        )


def test_audit_redaction_bounds_nested_credentials_and_collections() -> None:
    """Verify internal JSON redaction handles suffix keys and oversized values."""

    redacted = redact_audit_json(
        {
            "client_secret": "hidden",
            "long_text": "x" * 2_100,
            "many_items": list(range(101)),
        }
    )

    assert redacted["client_secret"] == "[REDACTED]"
    assert str(redacted["long_text"]).endswith("…")
    assert str(redacted["many_items"][-1]).startswith("[TRUNCATED_1_ITEMS]")


async def _seed_internal_audit_records(
    client: httpx.AsyncClient,
    database_session_factory: async_sessionmaker[AsyncSession],
) -> InternalAuditSeed:
    """Create one Run graph and three records for each internal audit resource."""

    run = await create_test_run(client)
    task_response = await client.post(
        f"/api/v1/runs/{run['run_id']}/tasks",
        json={
            "task_type": "audit_test",
            "title": "Audit test",
            "objective": "Verify internal query contracts",
        },
    )
    assert task_response.status_code == 201
    task = task_response.json()
    model_attempt_response = await client.post(
        f"/api/v1/runs/{run['run_id']}/attempts",
        json={
            "task_id": task["task_id"],
            "provider": "openai",
            "model": "test-model",
            "status": "completed",
        },
    )
    assert model_attempt_response.status_code == 201
    model_attempt = model_attempt_response.json()

    async with database_session_factory() as db_session:
        model_tool_calls = [
            LlmModelToolCall(
                attempt_id=model_attempt["attempt_id"],
                tool_name="read_file" if index < 2 else "run_command",
                risk_level="read" if index < 2 else "execute",
                input_json={
                    "path": f"/workspace/file-{index}.txt",
                    "api_key": "must-not-leak",
                    "nested": {"access_token": "also-secret"},
                },
                status="completed",
                permission_decision="allowed",
                result_uri=f"minio://private/tool-result-{index}.json",
                error_message="x" * 700 if index == 0 else None,
            )
            for index in range(3)
        ]
        task_evaluations = [
            LlmTaskEvaluation(
                run_id=run["run_id"],
                task_id=task["task_id"],
                candidate_attempt_id=model_attempt["attempt_id"],
                evaluator_attempt_id=None,
                evaluation_type="deterministic" if index < 2 else "model_review",
                verdict="pass" if index < 2 else "fail",
                score="95.00" if index < 2 else "40.00",
                findings_json={
                    "summary": f"finding-{index}",
                    "password": "must-not-leak",
                },
            )
            for index in range(3)
        ]
        outbox_events = [
            LlmOutboxEvent(
                aggregate_type="task",
                aggregate_id=task["task_id"],
                event_type="task.ready" if index < 2 else "task.cancel_requested",
                payload_json={
                    "message_id": f"message-{index}",
                    "task_id": task["task_id"],
                    "authorization": "must-not-leak",
                },
                status="pending" if index < 2 else "published",
                publish_attempts=index,
            )
            for index in range(3)
        ]
        db_session.add_all(model_tool_calls + task_evaluations + outbox_events)
        await db_session.commit()
        return InternalAuditSeed(
            run_id=run["run_id"],
            task_id=task["task_id"],
            model_attempt_id=model_attempt["attempt_id"],
            model_tool_call_ids=[
                model_tool_call.tool_call_id for model_tool_call in model_tool_calls
            ],
            task_evaluation_ids=[
                task_evaluation.evaluation_id for task_evaluation in task_evaluations
            ],
            outbox_event_ids=[outbox_event.event_id for outbox_event in outbox_events],
        )


async def test_internal_routes_require_both_authentication_boundaries(
    client: httpx.AsyncClient,
) -> None:
    """Verify the public API key cannot authorize internal audit reads by itself."""

    missing_internal_key = await client.get("/api/v1/internal/tool-calls")
    wrong_internal_key = await client.get(
        "/api/v1/internal/tool-calls",
        headers={"X-Internal-API-Key": "wrong-internal-key-long-enough"},
    )
    missing_public_key = await client.get(
        "/api/v1/internal/tool-calls",
        headers={"X-API-Key": "", **INTERNAL_HEADERS},
    )

    assert missing_internal_key.status_code == 401
    assert wrong_internal_key.status_code == 401
    assert missing_public_key.status_code == 401


async def test_model_tool_call_queries_are_scoped_paginated_and_redacted(
    client: httpx.AsyncClient,
    test_database_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Verify model tool-call lists omit inputs and details redact credential fields."""

    seed = await _seed_internal_audit_records(client, test_database_session_factory)
    first = (
        await client.get(
            "/api/v1/internal/tool-calls",
            headers=INTERNAL_HEADERS,
            params={"attempt_id": seed.model_attempt_id, "limit": 2},
        )
    ).json()
    second = (
        await client.get(
            "/api/v1/internal/tool-calls",
            headers=INTERNAL_HEADERS,
            params={
                "attempt_id": seed.model_attempt_id,
                "limit": 2,
                "cursor": first["next_cursor"],
            },
        )
    ).json()
    assert {
        model_tool_call["tool_call_id"]
        for model_tool_call in first["items"] + second["items"]
    } == set(seed.model_tool_call_ids)
    assert all("input_json" not in model_tool_call for model_tool_call in first["items"])
    assert all("result_uri" not in model_tool_call for model_tool_call in first["items"])

    replay = await client.get(
        "/api/v1/internal/tool-calls",
        headers=INTERNAL_HEADERS,
        params={
            "attempt_id": seed.model_attempt_id,
            "status": "failed",
            "cursor": first["next_cursor"],
        },
    )
    assert replay.status_code == 422
    detail = (
        await client.get(
            f"/api/v1/internal/tool-calls/{seed.model_tool_call_ids[0]}",
            headers=INTERNAL_HEADERS,
        )
    ).json()
    assert detail["input_json"]["api_key"] == "[REDACTED]"
    assert detail["input_json"]["nested"]["access_token"] == "[REDACTED]"
    assert len(detail["error_message_preview"]) == 500
    assert detail["has_result"] is True
    assert "result_uri" not in detail


async def test_task_evaluation_queries_validate_scope_and_redact_findings(
    client: httpx.AsyncClient,
    test_database_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Verify Task evaluation queries enforce Run ownership and redact findings."""

    seed = await _seed_internal_audit_records(client, test_database_session_factory)
    page = await client.get(
        "/api/v1/internal/evaluations",
        headers=INTERNAL_HEADERS,
        params={"run_id": seed.run_id, "evaluation_type": "deterministic"},
    )
    assert page.status_code == 200
    assert len(page.json()["items"]) == 2
    assert all("findings_json" not in item for item in page.json()["items"])
    detail = (
        await client.get(
            f"/api/v1/internal/evaluations/{seed.task_evaluation_ids[0]}",
            headers=INTERNAL_HEADERS,
        )
    ).json()
    assert detail["findings_json"]["password"] == "[REDACTED]"

    foreign_run = await create_test_run(client)
    invalid_scope = await client.get(
        "/api/v1/internal/evaluations",
        headers=INTERNAL_HEADERS,
        params={"run_id": foreign_run["run_id"], "task_id": seed.task_id},
    )
    assert invalid_scope.status_code == 422


async def test_outbox_queries_are_paginated_filter_bound_and_redacted(
    client: httpx.AsyncClient,
    test_database_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Verify infrastructure outbox facts are readable without exposing credentials."""

    seed = await _seed_internal_audit_records(client, test_database_session_factory)
    first = (
        await client.get(
            "/api/v1/internal/outbox-events",
            headers=INTERNAL_HEADERS,
            params={"aggregate_id": seed.task_id, "limit": 2},
        )
    ).json()
    second = (
        await client.get(
            "/api/v1/internal/outbox-events",
            headers=INTERNAL_HEADERS,
            params={
                "aggregate_id": seed.task_id,
                "limit": 2,
                "cursor": first["next_cursor"],
            },
        )
    ).json()
    assert {
        outbox_event["event_id"]
        for outbox_event in first["items"] + second["items"]
    } == set(seed.outbox_event_ids)
    assert all("payload_json" not in outbox_event for outbox_event in first["items"])

    replay = await client.get(
        "/api/v1/internal/outbox-events",
        headers=INTERNAL_HEADERS,
        params={
            "aggregate_id": seed.task_id,
            "status": "pending",
            "cursor": first["next_cursor"],
        },
    )
    assert replay.status_code == 422
    detail = (
        await client.get(
            f"/api/v1/internal/outbox-events/{seed.outbox_event_ids[0]}",
            headers=INTERNAL_HEADERS,
        )
    ).json()
    assert detail["payload_json"]["authorization"] == "[REDACTED]"
