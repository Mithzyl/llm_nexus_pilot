"""Deterministic Evaluation rule, scope, and idempotency behavior tests."""

import httpx
from test_api import create_test_run

INTERNAL_HEADERS = {"X-Internal-API-Key": "test-internal-key-long-enough"}


async def create_task(client: httpx.AsyncClient, run_id: str, suffix: str) -> dict:
    """Create one task owned by the selected Run for Evaluation tests."""

    response = await client.post(
        f"/api/v1/runs/{run_id}/tasks",
        json={
            "task_type": "evaluation_test",
            "title": f"Evaluation {suffix}",
            "objective": "Verify deterministic evaluation behavior",
        },
    )
    assert response.status_code == 201
    return response.json()


async def create_rule_set(
    client: httpx.AsyncClient,
    *,
    name: str,
    rules: list[dict],
) -> httpx.Response:
    """Create one deterministic Evaluation rule set through the internal API."""

    return await client.post(
        "/api/v1/internal/evaluation-rule-sets",
        headers=INTERNAL_HEADERS,
        json={
            "name": name,
            "schema_version": "v1",
            "rules": rules,
        },
    )


async def test_evaluation_rule_set_rejects_unknown_rules_and_invalid_config(
    client: httpx.AsyncClient,
) -> None:
    """Verify unsupported or malformed rules cannot enter an enabled rule set."""

    unknown = await create_rule_set(
        client,
        name="unknown-rule",
        rules=[
            {
                "rule_key": "unknown",
                "rule_type": "always_pass",
                "severity": "error",
                "config_json": {},
            }
        ],
    )
    invalid_limit = await create_rule_set(
        client,
        name="invalid-limit",
        rules=[
            {
                "rule_key": "length",
                "rule_type": "input_length",
                "severity": "error",
                "config_json": {"max_characters": 0},
            }
        ],
    )

    assert unknown.status_code == 422
    assert invalid_limit.status_code == 422


async def test_evaluation_idempotency_rejects_same_key_with_different_input(
    client: httpx.AsyncClient,
) -> None:
    """Verify an idempotency key cannot silently return another input's verdict."""

    run = await create_test_run(client)
    task = await create_task(client, run["run_id"], "idempotency")
    rule_set = await create_rule_set(
        client,
        name="idempotency-rules",
        rules=[
            {
                "rule_key": "length",
                "rule_type": "input_length",
                "severity": "error",
                "config_json": {"max_characters": 20},
            }
        ],
    )
    assert rule_set.status_code == 201
    base_payload = {
        "run_id": run["run_id"],
        "task_id": task["task_id"],
        "evaluation_type": "deterministic",
        "rule_set_id": rule_set.json()["rule_set_id"],
        "idempotency_key": "evaluation-idempotency",
    }

    first = await client.post(
        "/api/v1/evaluations",
        json={**base_payload, "input_text": "short"},
    )
    replay = await client.post(
        "/api/v1/evaluations",
        json={**base_payload, "input_text": "short"},
    )
    conflict = await client.post(
        "/api/v1/evaluations",
        json={**base_payload, "input_text": "different"},
    )

    assert first.status_code == 201
    assert replay.status_code == 200
    assert replay.json()["evaluation_id"] == first.json()["evaluation_id"]
    assert conflict.status_code == 409


async def test_evaluation_candidate_attempt_must_belong_to_selected_task(
    client: httpx.AsyncClient,
) -> None:
    """Verify a same-Run Attempt cannot be evaluated under an unrelated Task."""

    run = await create_test_run(client)
    first_task = await create_task(client, run["run_id"], "first")
    second_task = await create_task(client, run["run_id"], "second")
    attempt = await client.post(
        f"/api/v1/runs/{run['run_id']}/attempts",
        json={
            "task_id": first_task["task_id"],
            "provider": "openai",
            "model": "test-model",
            "status": "completed",
        },
    )
    assert attempt.status_code == 201
    rule_set = await create_rule_set(
        client,
        name="attempt-scope-rules",
        rules=[
            {
                "rule_key": "attempt",
                "rule_type": "attempt_reference",
                "severity": "error",
                "config_json": {},
            }
        ],
    )
    assert rule_set.status_code == 201

    evaluation = await client.post(
        "/api/v1/evaluations",
        json={
            "run_id": run["run_id"],
            "task_id": second_task["task_id"],
            "candidate_attempt_id": attempt.json()["attempt_id"],
            "evaluation_type": "deterministic",
            "rule_set_id": rule_set.json()["rule_set_id"],
            "idempotency_key": "cross-task-attempt",
        },
    )

    assert evaluation.status_code == 409
