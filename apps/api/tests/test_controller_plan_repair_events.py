"""Verify durable repair events, their timing, replay and public payloads without paid calls."""

import json

import pytest
from test_agent_workflows import (
    register_default_agent_test_model,  # noqa: F401
    scripted_registry,
    workflow_payload,
)
from test_api import create_test_run
from test_controller_plan_repair import InterruptedPlanRepairProvider, PlanRepairProvider

from nexuspilot_api.core.dependencies import get_provider_registry
from nexuspilot_api.main import app


async def read_events(client, workflow_id, after=0):
    """Read committed public events through the same SSE replay endpoint used by clients."""
    response = await client.get(
        f"/api/v1/agent-workflows/{workflow_id}/events?after_sequence={after}"
    )
    assert response.status_code == 200
    return [
        json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")
    ]


@pytest.mark.parametrize(
    "mode,persistent,options,expected,reason",
    [
        ("duplicate_key", False, {}, ["requested", "completed"], None),
        ("duplicate_key", True, {}, ["requested", "skipped"], "repair_limit"),
        ("forbidden", False, {}, ["skipped"], "unrepairable_checks"),
        ("duplicate_key", False, {"max_model_calls": 6}, ["skipped"], "model_call_budget"),
        ("duplicate_key", False, {"max_nodes": 11}, ["skipped"], "node_budget"),
        (None, False, {}, [], None),
        ("duplicate_key", False, {"workflow_version": "1.0.0"}, [], None),
    ],
)
async def test_plan_events_are_durable_and_follow_validation(
    client, mode, persistent, options, expected, reason
):
    """Only validated repair emits completed; replay and repeated POST never add events."""
    run = await create_test_run(client)
    provider = PlanRepairProvider(invalid_mode=mode, persistent=persistent)
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)
    payload = workflow_payload(
        **{
            "workflow_version": "1.1.0",
            "execution_profile": "tool_enabled_v1",
            **options,
        }
    )
    url = f"/api/v1/runs/{run['run_id']}/agent-workflows"
    response = await client.post(url, json=payload)
    assert response.status_code == 201, response.text
    result = response.json()
    events = await read_events(client, result["workflow_execution_id"])
    repair = [e for e in events if e["event_type"].startswith("agent.controller.plan_repair.")]
    assert [e["event_type"].rsplit(".", 1)[1] for e in repair] == expected
    nodes = {n["node_execution_id"]: n for n in result["nodes"]}
    for event in repair:
        facts = event["public_payload"]
        assert facts["workflow_round"] == 1
        assert facts["failed_check_ids"]
        assert (
            nodes[facts["source_controller_node_execution_id"]]["node_key"] == "controller_planning"
        )
        assert nodes[facts["source_validation_node_execution_id"]]["output"]["is_valid"] is False
        assert "original_plan" not in facts and "validation" not in facts
        if event["event_type"].endswith("requested"):
            assert event["node_execution_id"] == facts["repair_node_execution_id"]
            assert event["node_status"] == "running"
        if event["event_type"].endswith("completed"):
            validation_id = facts["validation_node_execution_id"]
            assert nodes[validation_id]["output"]["is_valid"] is True
            assert facts["model_attempt_ids"]
            prior = [
                e
                for e in events
                if e["node_execution_id"] == validation_id
                and e["event_type"] == "agent.node.completed"
            ]
            assert prior[0]["event_sequence"] < event["event_sequence"]
    if reason:
        assert repair[-1]["public_payload"]["stop_reason"] == reason
    calls = len(provider.requests)
    repeated = await client.post(url, json=payload)
    assert repeated.status_code in (200, 201), repeated.text
    assert len(provider.requests) == calls
    assert await read_events(client, result["workflow_execution_id"]) == events
    if repair:
        cursor = repair[0]["event_sequence"]
        assert await read_events(client, result["workflow_execution_id"], cursor) == [
            e for e in events if e["event_sequence"] > cursor
        ]


async def test_unknown_repair_never_emits_completed(client):
    """A timed-out repair retains requested plus unknown node outcome, never false success."""
    run = await create_test_run(client)
    provider = InterruptedPlanRepairProvider(invalid_mode="duplicate_key")
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)
    response = await client.post(
        f"/api/v1/runs/{run['run_id']}/agent-workflows",
        json=workflow_payload(workflow_version="1.1.0"),
    )
    result = response.json()
    assert result["status"] == "outcome_unknown"
    events = await read_events(client, result["workflow_execution_id"])
    assert [e["event_type"] for e in events if "plan_repair" in e["event_type"]] == [
        "agent.controller.plan_repair.requested"
    ]


async def test_live_plan_repair_events_match_committed_replay(client):
    """The live POST stream and later GET expose the same ordered persisted repair facts."""
    run = await create_test_run(client)
    provider = PlanRepairProvider(invalid_mode="duplicate_key")
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)
    response = await client.post(
        f"/api/v1/runs/{run['run_id']}/agent-workflows",
        json=workflow_payload(workflow_version="1.1.0", stream=True),
    )
    assert response.status_code == 201, response.text
    streamed = [
        json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")
    ]
    assert streamed[-1]["event_type"] == "agent.workflow.completed"
    replayed = await read_events(client, streamed[0]["workflow_execution_id"])
    repair_events = [e for e in streamed if "plan_repair" in e["event_type"]]
    assert [e["event_type"] for e in repair_events] == [
        "agent.controller.plan_repair.requested",
        "agent.controller.plan_repair.completed",
    ]
    replayed_repairs = [e for e in replayed if "plan_repair" in e["event_type"]]
    # SQLite drops timezone metadata on reload; compare the persisted UTC value
    # explicitly while retaining exact equality for all other event fields.
    assert len(repair_events) == len(replayed_repairs)
    for live, saved in zip(repair_events, replayed_repairs, strict=True):
        assert live["occurred_at"].removesuffix("Z") == saved["occurred_at"]
        assert {k: v for k, v in live.items() if k != "occurred_at"} == {
            k: v for k, v in saved.items() if k != "occurred_at"
        }
