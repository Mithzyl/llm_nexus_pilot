"""Read only structural App Run diagnostics without model, tool or memory body disclosure."""

import pytest
from test_application_tool_loop import (  # noqa: F401
    app_headers,
    configured_application,
    remote_server,
)

from nexuspilot_api.models import LlmModelAttempt, LlmModelToolCall
from nexuspilot_api.models.enums import AttemptStatus


@pytest.mark.usefixtures("configured_application")
async def test_app_diagnostics_only_returns_owned_structural_fields(
    resource_client,
    test_database_session_factory,
):
    """Correlate bounded model/tool pages while omitting raw bodies and refusing foreign cursors."""
    headers = app_headers("personal_assistant")
    created = await resource_client.post(
        "/api/v1/app/runs", headers=headers, json={"user_request": "PRIVATE-QUESTION"}
    )
    assert created.status_code == 201
    run_id = created.json()["run_id"]
    async with test_database_session_factory() as db:
        attempts = []
        for _index in range(2):
            attempt = LlmModelAttempt(
                run_id=run_id,
                provider="openai",
                model="fixture",
                status=AttemptStatus.COMPLETED,
                input_tokens=10,
                output_tokens=2,
                latency_ms=7,
                error_message="PRIVATE-ERROR",
                raw_request_uri="private://request",
            )
            db.add(attempt)
            await db.flush()
            attempts.append(attempt.attempt_id)
            db.add(
                LlmModelToolCall(
                    attempt_id=attempt.attempt_id,
                    tool_name="query_memory",
                    tool_version="1.0.0",
                    risk_level="read_only",
                    input_json={"secret": "PRIVATE-INPUT"},
                    output_json={"secret": "PRIVATE-OUTPUT"},
                    status="completed",
                )
            )
        await db.commit()
    path = f"/api/v1/app/runs/{run_id}/diagnostics"
    first = await resource_client.get(path + "?limit=1", headers=headers)
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["application_id"] == "personal_assistant"
    assert body["workflow"] is None
    assert len(body["attempts"]["items"]) == len(body["tools"]["items"]) == 1
    assert body["attempts"]["next_cursor"] and body["tools"]["next_cursor"]
    assert "PRIVATE" not in first.text and "private://" not in first.text
    second = await resource_client.get(
        path,
        headers=headers,
        params={
            "limit": 1,
            "after_attempt_id": body["attempts"]["next_cursor"],
            "after_tool_call_id": body["tools"]["next_cursor"],
        },
    )
    assert second.status_code == 200
    assert second.json()["attempts"]["next_cursor"] is None
    assert (
        second.json()["attempts"]["items"][0]["attempt_id"]
        != body["attempts"]["items"][0]["attempt_id"]
    )
    denied = await resource_client.get(path, headers=app_headers("personal_assistant", "another"))
    assert denied.status_code == 404
    another = await resource_client.post(
        "/api/v1/app/runs", headers=headers, json={"user_request": "other run"}
    )
    wrong = await resource_client.get(
        f"/api/v1/app/runs/{another.json()['run_id']}/diagnostics",
        headers=headers,
        params={"after_attempt_id": attempts[0]},
    )
    assert wrong.status_code == 404
