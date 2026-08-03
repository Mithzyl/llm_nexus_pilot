"""Phase 2 L0-L4 Memory layer API contract tests."""

import httpx

INTERNAL_HEADERS = {"X-Internal-API-Key": "test-internal-key-long-enough"}


async def create_user(client: httpx.AsyncClient, user_id: str) -> None:
    """Create one active platform user."""

    response = await client.post(
        "/api/v1/users",
        json={"user_id": user_id, "display_name": user_id},
    )
    assert response.status_code == 201


async def create_session(client: httpx.AsyncClient, user_id: str) -> dict:
    """Create one conversation."""

    response = await client.post(
        "/api/v1/sessions",
        json={"user_id": user_id, "title": "Phase 2"},
    )
    assert response.status_code == 201
    return response.json()


async def append_message(client: httpx.AsyncClient, session_id: str, content: str) -> dict:
    """Append one user message."""

    response = await client.post(
        f"/api/v1/sessions/{session_id}/messages",
        json={"role": "user", "content_text": content},
    )
    assert response.status_code == 201
    return response.json()


async def create_run(
    client: httpx.AsyncClient, user_id: str, session_id: str | None = None
) -> dict:
    """Create one run."""

    payload = {"user_id": user_id, "user_request": "run phase 2"}
    if session_id:
        payload["session_id"] = session_id
    response = await client.post("/api/v1/runs", json=payload)
    assert response.status_code == 201
    return response.json()


async def create_task(client: httpx.AsyncClient, run_id: str) -> dict:
    """Create one task inside a run."""

    response = await client.post(
        f"/api/v1/runs/{run_id}/tasks",
        json={
            "task_type": "research",
            "title": "Research",
            "objective": "Gather evidence",
        },
    )
    assert response.status_code == 201
    return response.json()


def state_payload(**overrides: object) -> dict:
    """Return one valid session_state.v1 document."""

    payload = {
        "schema_version": "session_state.v1",
        "state_json": {
            "goal": "Understand memory",
            "constraints": [],
            "decisions": [{"decision": "Use MySQL", "source_message_ids": []}],
            "open_questions": [],
            "active_entities": [],
        },
        "expected_previous_version": 0,
        "idempotency_key": "state-1",
    }
    payload.update(overrides)
    return payload


async def test_l1_session_state_summary_and_version_switching(
    client: httpx.AsyncClient,
) -> None:
    """Verify L1 State/Summary generation, version switching, and stale reads."""

    await create_user(client, "l1-owner")
    conversation = await create_session(client, "l1-owner")
    session_id = conversation["session_id"]

    state = await client.post(
        f"/api/v1/internal/sessions/{session_id}/states",
        json=state_payload(),
        headers=INTERNAL_HEADERS,
    )
    assert state.status_code == 201
    state_body = state.json()
    assert state_body["version"] == 1
    assert state_body["status"] == "active"
    assert state_body["json_snapshot_object_id"]

    hidden_state_reasoning = await client.post(
        f"/api/v1/internal/sessions/{session_id}/states",
        json=state_payload(
            state_json={
                **state_payload()["state_json"],
                "chain_of_thought": "must not persist",
            },
            expected_previous_version=1,
            idempotency_key="state-hidden-reasoning",
        ),
        headers=INTERNAL_HEADERS,
    )
    assert hidden_state_reasoning.status_code == 422

    stale = await client.post(
        f"/api/v1/internal/sessions/{session_id}/states",
        json=state_payload(expected_previous_version=9, idempotency_key="state-2"),
        headers=INTERNAL_HEADERS,
    )
    assert stale.status_code == 409

    replay = await client.post(
        f"/api/v1/internal/sessions/{session_id}/states",
        json=state_payload(),
        headers=INTERNAL_HEADERS,
    )
    assert replay.status_code == 200
    assert replay.json()["session_state_id"] == state_body["session_state_id"]

    message = await append_message(client, session_id, "First message")
    summary = await client.post(
        f"/api/v1/sessions/{session_id}/summaries",
        json={
            "schema_version": "session_summary.v1",
            "summary_json": {
                "coverage": "messages 1..1",
                "confirmed_facts": ["first"],
                "decisions": [],
                "open_items": [],
                "artifacts": [],
            },
            "summary_from_message_sequence": 1,
            "summary_through_message_sequence": 1,
            "summary_through_message_id": message["message_id"],
            "model": "test-model",
            "expected_previous_version": 0,
            "idempotency_key": "summary-1",
        },
    )
    assert summary.status_code == 201

    oversized_summary = await client.post(
        f"/api/v1/sessions/{session_id}/summaries",
        json={
            "schema_version": "session_summary.v1",
            "summary_json": {
                "coverage": "x" * 1_600,
                "confirmed_facts": [],
                "decisions": [],
                "open_items": [],
                "artifacts": [],
            },
            "summary_from_message_sequence": 1,
            "summary_through_message_sequence": 1,
            "summary_through_message_id": message["message_id"],
            "model": "test-model",
            "expected_previous_version": 1,
            "idempotency_key": "summary-oversized",
        },
    )
    assert oversized_summary.status_code == 422
    summary_body = summary.json()
    assert summary_body["version"] == 1
    assert summary_body["json_snapshot_object_id"]

    view = await client.get(f"/api/v1/sessions/{session_id}/memory")
    assert view.status_code == 200
    view_body = view.json()
    assert view_body["is_stale"] is False
    assert view_body["current_state"]["version"] == 1
    assert view_body["current_summary"]["version"] == 1

    summaries = await client.get(f"/api/v1/sessions/{session_id}/summaries")
    assert summaries.status_code == 200
    assert len(summaries.json()["items"]) == 1


async def test_l1_session_summary_range_and_foreign_message_rejected(
    client: httpx.AsyncClient,
) -> None:
    """Verify summary ranges are monotonic and messages must belong to the session."""

    await create_user(client, "l1-range-owner")
    first = await create_session(client, "l1-range-owner")
    second = await create_session(client, "l1-range-owner")
    foreign_message = await append_message(client, second["session_id"], "foreign")

    invalid_range = await client.post(
        f"/api/v1/sessions/{first['session_id']}/summaries",
        json={
            "schema_version": "session_summary.v1",
            "summary_json": {
                "coverage": "x",
                "confirmed_facts": [],
                "decisions": [],
                "open_items": [],
                "artifacts": [],
            },
            "summary_from_message_sequence": 5,
            "summary_through_message_sequence": 2,
            "model": "test-model",
            "idempotency_key": "range-1",
        },
    )
    assert invalid_range.status_code == 422

    foreign = await client.post(
        f"/api/v1/sessions/{first['session_id']}/summaries",
        json={
            "schema_version": "session_summary.v1",
            "summary_json": {
                "coverage": "x",
                "confirmed_facts": [],
                "decisions": [],
                "open_items": [],
                "artifacts": [],
            },
            "summary_from_message_sequence": 1,
            "summary_through_message_sequence": 1,
            "summary_through_message_id": foreign_message["message_id"],
            "model": "test-model",
            "idempotency_key": "foreign-1",
        },
    )
    assert foreign.status_code == 409


async def test_l0_agent_working_memory_checkpoint_lifecycle(
    client: httpx.AsyncClient,
) -> None:
    """Verify L0 check points, version conflicts, recovery reads, and finalization."""

    await create_user(client, "l0-owner")
    conversation = await create_session(client, "l0-owner")
    run = await create_run(client, "l0-owner", conversation["session_id"])
    task = await create_task(client, run["run_id"])

    agent_run = await client.post(
        "/api/v1/internal/agent-runs",
        json={
            "run_id": run["run_id"],
            "task_id": task["task_id"],
            "agent_role": "researcher",
        },
        headers=INTERNAL_HEADERS,
    )
    assert agent_run.status_code == 201
    agent_run_id = agent_run.json()["agent_run_id"]

    checkpoint_payload = {
        "schema_version": "agent_working_state.v1",
        "state_json": {
            "current_objective": "Find evidence",
            "searched_queries": ["memory"],
            "files_read": [{"file_reference": "a.txt", "content_hash": "abc"}],
            "rejected_hypotheses": [],
            "next_actions": ["read b.txt"],
            "pending_tool_call_ids": [],
            "remaining_budget": {"input_tokens": 100, "tool_calls": 5, "time_seconds": 60},
            "tentative_findings": [],
            "blocked_on": [],
        },
        "expected_previous_version": 0,
        "tokenizer_name": "utf8_upper_bound",
        "tokenizer_version": "utf8_bytes_upper_bound_v1",
        "idempotency_key": "checkpoint-1",
    }
    checkpoint = await client.post(
        f"/api/v1/internal/agent-runs/{agent_run_id}/working-memory/checkpoints",
        json=checkpoint_payload,
        headers=INTERNAL_HEADERS,
    )
    assert checkpoint.status_code == 201
    assert checkpoint.json()["version"] == 1

    stale = await client.post(
        f"/api/v1/internal/agent-runs/{agent_run_id}/working-memory/checkpoints",
        json={
            **checkpoint_payload,
            "idempotency_key": "checkpoint-2",
            "expected_previous_version": 9,
        },
        headers=INTERNAL_HEADERS,
    )
    assert stale.status_code == 409

    replay = await client.post(
        f"/api/v1/internal/agent-runs/{agent_run_id}/working-memory/checkpoints",
        json=checkpoint_payload,
        headers=INTERNAL_HEADERS,
    )
    assert replay.status_code == 200

    current = await client.get(
        f"/api/v1/internal/agent-runs/{agent_run_id}/working-memory",
        headers=INTERNAL_HEADERS,
    )
    assert current.status_code == 200
    assert current.json()["state_json"]["current_objective"] == "Find evidence"

    hidden_reasoning = await client.post(
        f"/api/v1/internal/agent-runs/{agent_run_id}/working-memory/checkpoints",
        json={
            **checkpoint_payload,
            "idempotency_key": "checkpoint-3",
            "state_json": {
                **checkpoint_payload["state_json"],
                "chain_of_thought": "secret",
            },
        },
        headers=INTERNAL_HEADERS,
    )
    assert hidden_reasoning.status_code == 422

    unsupported_tokenizer = await client.post(
        f"/api/v1/internal/agent-runs/{agent_run_id}/working-memory/checkpoints",
        json={
            **checkpoint_payload,
            "idempotency_key": "checkpoint-unsupported-tokenizer",
            "tokenizer_name": "unimplemented-tokenizer",
        },
        headers=INTERNAL_HEADERS,
    )
    assert unsupported_tokenizer.status_code == 422

    nonterminal = await client.post(
        f"/api/v1/internal/agent-runs/{agent_run_id}/status",
        params={"status": "running"},
        headers=INTERNAL_HEADERS,
    )
    assert nonterminal.status_code == 422

    finished = await client.post(
        f"/api/v1/internal/agent-runs/{agent_run_id}/status",
        params={"status": "completed"},
        headers=INTERNAL_HEADERS,
    )
    assert finished.status_code == 200
    assert finished.json()["status"] == "completed"

    after_finish = await client.post(
        f"/api/v1/internal/agent-runs/{agent_run_id}/working-memory/checkpoints",
        json={**checkpoint_payload, "idempotency_key": "checkpoint-4"},
        headers=INTERNAL_HEADERS,
    )
    assert after_finish.status_code == 409


async def test_l2_handoff_and_run_memory_limits(client: httpx.AsyncClient) -> None:
    """Verify Handoff validation and bounded deterministic Run Snapshot merging."""

    await create_user(client, "l2-owner")
    conversation = await create_session(client, "l2-owner")
    run = await create_run(client, "l2-owner", conversation["session_id"])
    task = await create_task(client, run["run_id"])
    agent_run = await client.post(
        "/api/v1/internal/agent-runs",
        json={
            "run_id": run["run_id"],
            "task_id": task["task_id"],
            "agent_role": "researcher",
        },
        headers=INTERNAL_HEADERS,
    )
    agent_run_id = agent_run.json()["agent_run_id"]

    handoff_json = {
        "objective": "Research memory layers",
        "status": "completed",
        "confirmed_facts": ["L0 is private"],
        "decisions": ["Use MySQL as truth"],
        "files_read": ["docs.md"],
        "files_changed": [],
        "artifacts": ["artifact-1"],
        "tests": [],
        "remaining_work": [],
        "risks": ["storage"],
        "unknowns": [],
        "invariants_for_next_agent": ["Never read L0"],
    }
    handoff = await client.post(
        f"/api/v1/internal/runs/{run['run_id']}/agent-handoffs",
        json={
            "agent_run_id": agent_run_id,
            "task_id": task["task_id"],
            "schema_version": "agent_handoff.v1",
            "status": "completed",
            "handoff_json": handoff_json,
            "idempotency_key": "handoff-1",
        },
        headers=INTERNAL_HEADERS,
    )
    assert handoff.status_code == 201
    handoff_id = handoff.json()["agent_handoff_id"]

    empty_objective = await client.post(
        f"/api/v1/internal/runs/{run['run_id']}/agent-handoffs",
        json={
            "agent_run_id": agent_run_id,
            "schema_version": "agent_handoff.v1",
            "status": "completed",
            "handoff_json": {**handoff_json, "objective": ""},
            "idempotency_key": "handoff-2",
        },
        headers=INTERNAL_HEADERS,
    )
    assert empty_objective.status_code == 422

    hidden_handoff_reasoning = await client.post(
        f"/api/v1/internal/runs/{run['run_id']}/agent-handoffs",
        json={
            "agent_run_id": agent_run_id,
            "schema_version": "agent_handoff.v1",
            "status": "completed",
            "handoff_json": {**handoff_json, "chain_of_thought": "must not persist"},
            "idempotency_key": "handoff-hidden-reasoning",
        },
        headers=INTERNAL_HEADERS,
    )
    assert hidden_handoff_reasoning.status_code == 422

    mismatched_handoff_status = await client.post(
        f"/api/v1/internal/runs/{run['run_id']}/agent-handoffs",
        json={
            "agent_run_id": agent_run_id,
            "schema_version": "agent_handoff.v1",
            "status": "partial",
            "handoff_json": handoff_json,
            "idempotency_key": "handoff-status-mismatch",
        },
        headers=INTERNAL_HEADERS,
    )
    assert mismatched_handoff_status.status_code == 422

    oversized_handoff = await client.post(
        f"/api/v1/internal/runs/{run['run_id']}/agent-handoffs",
        json={
            "agent_run_id": agent_run_id,
            "schema_version": "agent_handoff.v1",
            "status": "completed",
            "handoff_json": {**handoff_json, "objective": "x" * 1_200},
            "idempotency_key": "handoff-oversized",
        },
        headers=INTERNAL_HEADERS,
    )
    assert oversized_handoff.status_code == 422

    rebuild = await client.post(
        f"/api/v1/internal/runs/{run['run_id']}/memory/rebuild",
        json={
            "expected_previous_version": 0,
            "tokenizer_name": "utf8_upper_bound",
            "tokenizer_version": "utf8_bytes_upper_bound_v1",
            "idempotency_key": "snapshot-1",
        },
        headers=INTERNAL_HEADERS,
    )
    assert rebuild.status_code == 201
    snapshot = rebuild.json()
    assert snapshot["version"] == 1
    assert snapshot["status"] == "active"
    assert "L0 is private" in str(snapshot["state_json"])

    rebuild_conflict = await client.post(
        f"/api/v1/internal/runs/{run['run_id']}/memory/rebuild",
        json={
            "expected_previous_version": 7,
            "tokenizer_name": "utf8_upper_bound",
            "tokenizer_version": "utf8_bytes_upper_bound_v1",
            "idempotency_key": "snapshot-2",
        },
        headers=INTERNAL_HEADERS,
    )
    assert rebuild_conflict.status_code == 409

    run_memory = await client.get(
        f"/api/v1/internal/runs/{run['run_id']}/memory",
        headers=INTERNAL_HEADERS,
    )
    assert run_memory.status_code == 200
    assert run_memory.json()["run_memory_snapshot_id"] == snapshot["run_memory_snapshot_id"]

    for handoff_number in range(3):
        additional_handoff = await client.post(
            f"/api/v1/internal/runs/{run['run_id']}/agent-handoffs",
            json={
                "agent_run_id": agent_run_id,
                "schema_version": "agent_handoff.v1",
                "status": "completed",
                "handoff_json": {
                    **handoff_json,
                    "objective": f"{handoff_number}:" + "x" * 450,
                },
                "idempotency_key": f"handoff-budget-{handoff_number}",
            },
            headers=INTERNAL_HEADERS,
        )
        assert additional_handoff.status_code == 201

    oversized_run_memory = await client.post(
        f"/api/v1/internal/runs/{run['run_id']}/memory/rebuild",
        json={
            "expected_previous_version": 1,
            "tokenizer_name": "utf8_upper_bound",
            "tokenizer_version": "utf8_bytes_upper_bound_v1",
            "idempotency_key": "snapshot-oversized",
        },
        headers=INTERNAL_HEADERS,
    )
    assert oversized_run_memory.status_code == 422

    handoffs = await client.get(
        f"/api/v1/internal/runs/{run['run_id']}/agent-handoffs",
        headers=INTERNAL_HEADERS,
    )
    assert handoffs.status_code == 200
    assert len(handoffs.json()["items"]) == 4
    assert handoffs.json()["items"][0]["agent_handoff_id"] == handoff_id


async def test_l3_project_scope_policy_and_profile(client: httpx.AsyncClient) -> None:
    """Verify Project defaults to disabled Memory and explicit policy transitions."""

    await create_user(client, "l3-owner")
    created = await client.post(
        "/api/v1/projects",
        json={"owner_user_id": "l3-owner", "project_name": "Nexus"},
        headers=INTERNAL_HEADERS,
    )
    assert created.status_code == 201
    project = created.json()
    assert project["memory_status"] == "disabled"
    project_id = project["project_id"]

    listing = await client.get("/api/v1/projects", params={"owner_user_id": "l3-owner"})
    assert listing.status_code == 200
    assert listing.json()["items"][0]["project_id"] == project_id

    enabled = await client.patch(
        f"/api/v1/projects/{project_id}/memory-policy",
        json={"action": "enable", "actor_id": "caller-1"},
        headers=INTERNAL_HEADERS,
    )
    assert enabled.status_code == 200
    assert enabled.json()["memory_status"] == "enabled"

    bad_workspace = await client.post(
        f"/api/v1/projects/{project_id}/workspaces",
        json={
            "workspace_type": "git",
            "credential_free_locator": "https://user:token@example.com/repo.git",
        },
        headers=INTERNAL_HEADERS,
    )
    assert bad_workspace.status_code == 422

    workspace = await client.post(
        f"/api/v1/projects/{project_id}/workspaces",
        json={
            "workspace_type": "git",
            "credential_free_locator": "https://example.com/repo.git",
        },
        headers=INTERNAL_HEADERS,
    )
    assert workspace.status_code == 201
    assert workspace.json()["locator_hash"]

    candidate = await client.post(
        "/api/v1/memories",
        json={
            "user_id": "l3-owner",
            "project_id": project_id,
            "memory_type": "project_decision",
            "content_text": "Use MySQL as the fact store",
            "status": "candidate",
            "semantic_key": "decision:storage",
            "idempotency_key": "project-memory-1",
            "sources": [{"source_type": "trusted_request", "source_resource_id": "request-1"}],
        },
    )
    assert candidate.status_code == 201
    memory_id = candidate.json()["memory_id"]

    candidates = await client.get(
        f"/api/v1/projects/{project_id}/memory-candidates",
    )
    assert candidates.status_code == 200
    assert len(candidates.json()["items"]) == 1

    approved = await client.post(
        f"/api/v1/projects/{project_id}/memory-candidates/{memory_id}/approve",
        json={"expected_version_number": 1, "idempotency_key": "approve-1"},
        headers=INTERNAL_HEADERS,
    )
    assert approved.status_code == 200
    assert approved.json()["status"] == "active"
    assert approved.json()["approval_method"] == "trusted_caller"

    profile_before_rebuild = await client.get(f"/api/v1/projects/{project_id}/memory")
    assert profile_before_rebuild.status_code == 404
    rebuilt = await client.post(
        f"/api/v1/projects/{project_id}/memory/rebuild",
        json={"expected_previous_version": 0, "idempotency_key": "project-profile-1"},
        headers=INTERNAL_HEADERS,
    )
    assert rebuilt.status_code == 201

    profile = await client.get(f"/api/v1/projects/{project_id}/memory")
    assert profile.status_code == 200
    assert profile.json()["estimated_token_count"] > 0
    assert "MySQL" in str(profile.json()["profile_json"])


async def test_l3_project_fact_requires_project_scope(client: httpx.AsyncClient) -> None:
    """Verify project types demand project_id and non-project types forbid it."""

    await create_user(client, "l3-scope-owner")
    created = await client.post(
        "/api/v1/projects",
        json={"owner_user_id": "l3-scope-owner", "project_name": "Scope"},
        headers=INTERNAL_HEADERS,
    )
    project_id = created.json()["project_id"]

    missing_project = await client.post(
        "/api/v1/memories",
        json={
            "user_id": "l3-scope-owner",
            "memory_type": "project_rule",
            "content_text": "Must not leak",
            "idempotency_key": "scope-1",
            "sources": [{"source_type": "trusted_request", "source_resource_id": "r1"}],
        },
    )
    assert missing_project.status_code == 422

    forbidden_project = await client.post(
        "/api/v1/memories",
        json={
            "user_id": "l3-scope-owner",
            "project_id": project_id,
            "memory_type": "user_fact",
            "content_text": "Should be rejected",
            "idempotency_key": "scope-2",
            "sources": [{"source_type": "trusted_request", "source_resource_id": "r2"}],
        },
    )
    assert forbidden_project.status_code == 422


async def test_l4_user_candidates_approve_reject_and_profile(client: httpx.AsyncClient) -> None:
    """Verify L4 candidate decisions, profile eligibility, and rebuild behavior."""

    await create_user(client, "l4-owner")
    preference = await client.post(
        "/api/v1/memories",
        json={
            "user_id": "l4-owner",
            "memory_type": "user_preference",
            "content_text": "Always respond in Chinese",
            "status": "candidate",
            "semantic_key": "preference:language",
            "is_core_profile_eligible": True,
            "idempotency_key": "l4-pref-1",
            "sources": [
                {
                    "source_type": "message",
                    "source_resource_id": "",
                    "trust_level": "direct_user_statement",
                }
            ],
        },
    )
    assert preference.status_code == 422  # missing message source resource id

    # Build a real message source for the preference.
    conversation = await create_session(client, "l4-owner")
    message = await append_message(client, conversation["session_id"], "Always Chinese")
    preference = await client.post(
        "/api/v1/memories",
        json={
            "user_id": "l4-owner",
            "memory_type": "user_preference",
            "content_text": "Always respond in Chinese",
            "status": "candidate",
            "semantic_key": "preference:language",
            "is_core_profile_eligible": True,
            "idempotency_key": "l4-pref-2",
            "sources": [
                {
                    "source_type": "message",
                    "source_resource_id": message["message_id"],
                    "trust_level": "direct_user_statement",
                }
            ],
        },
    )
    assert preference.status_code == 201
    memory_id = preference.json()["memory_id"]

    candidates = await client.get("/api/v1/users/l4-owner/memory-candidates")
    assert candidates.status_code == 200
    assert len(candidates.json()["items"]) == 1

    profile_before = await client.get("/api/v1/users/l4-owner/memory-profile")
    assert profile_before.status_code == 404

    approved = await client.post(
        f"/api/v1/users/l4-owner/memory-candidates/{memory_id}/approve",
        json={"expected_version_number": 1, "idempotency_key": "l4-approve-1"},
        headers=INTERNAL_HEADERS,
    )
    assert approved.status_code == 200
    assert approved.json()["status"] == "active"

    still_absent = await client.get("/api/v1/users/l4-owner/memory-profile")
    assert still_absent.status_code == 404
    rebuilt = await client.post(
        "/api/v1/users/l4-owner/memory-profile/rebuild",
        json={
            "expected_previous_version": 0,
            "tokenizer_name": "utf8_upper_bound",
            "tokenizer_version": "utf8_bytes_upper_bound_v1",
            "idempotency_key": "user-profile-1",
        },
        headers=INTERNAL_HEADERS,
    )
    assert rebuilt.status_code == 201

    profile = await client.get("/api/v1/users/l4-owner/memory-profile")
    assert profile.status_code == 200
    assert "Chinese" in str(profile.json()["profile_json"])

    rejected = await client.post(
        "/api/v1/memories",
        json={
            "user_id": "l4-owner",
            "memory_type": "user_fact",
            "content_text": "Model guessed fact",
            "status": "candidate",
            "semantic_key": "fact:guess",
            "idempotency_key": "l4-fact-1",
            "sources": [{"source_type": "trusted_request", "source_resource_id": "r3"}],
        },
    )
    assert rejected.status_code == 201
    rejected_id = rejected.json()["memory_id"]
    reject = await client.post(
        f"/api/v1/users/l4-owner/memory-candidates/{rejected_id}/reject",
        json={"expected_version_number": 1, "idempotency_key": "l4-reject-1"},
        headers=INTERNAL_HEADERS,
    )
    assert reject.status_code == 200
    assert reject.json()["status"] == "rejected"


async def test_user_profile_hard_cap_includes_serialized_structure(
    client: httpx.AsyncClient,
) -> None:
    """Verify Profile selection accounts for JSON structure instead of content alone."""

    await create_user(client, "profile-cap-owner")
    conversation = await create_session(client, "profile-cap-owner")
    message = await append_message(client, conversation["session_id"], "x" * 750)
    memory = await client.post(
        "/api/v1/memories",
        json={
            "user_id": "profile-cap-owner",
            "memory_type": "user_preference",
            "content_text": "x" * 750,
            "status": "active",
            "semantic_key": "preference:large",
            "is_core_profile_eligible": True,
            "idempotency_key": "profile-cap-memory",
            "sources": [
                {
                    "source_type": "message",
                    "source_resource_id": message["message_id"],
                    "trust_level": "direct_user_statement",
                }
            ],
        },
    )
    assert memory.status_code == 201

    rebuilt = await client.post(
        "/api/v1/users/profile-cap-owner/memory-profile/rebuild",
        json={
            "expected_previous_version": 0,
            "tokenizer_name": "utf8_upper_bound",
            "tokenizer_version": "utf8_bytes_upper_bound_v1",
            "idempotency_key": "profile-cap-rebuild",
        },
        headers=INTERNAL_HEADERS,
    )
    assert rebuilt.status_code == 201
    assert rebuilt.json()["estimated_token_count"] <= 800
    assert "x" * 750 not in str(rebuilt.json()["profile_json"])


async def test_memory_credential_rejection_and_mutation_audit(
    client: httpx.AsyncClient,
) -> None:
    """Verify credentials are rejected before persistence and mutations are audited."""

    await create_user(client, "audit-owner")
    credential_memory = await client.post(
        "/api/v1/memories",
        json={
            "user_id": "audit-owner",
            "memory_type": "user_fact",
            "content_text": "password = hunter2secretvalue",
            "idempotency_key": "cred-1",
            "sources": [{"source_type": "trusted_request", "source_resource_id": "r1"}],
        },
    )
    assert credential_memory.status_code == 422
    assert "hunter2secretvalue" not in credential_memory.text

    created = await client.post(
        "/api/v1/memories",
        json={
            "user_id": "audit-owner",
            "memory_type": "user_fact",
            "content_text": "Safe fact",
            "idempotency_key": "audit-1",
            "sources": [{"source_type": "trusted_request", "source_resource_id": "r1"}],
        },
    )
    assert created.status_code == 201
    memory_id = created.json()["memory_id"]

    corrected = await client.patch(
        f"/api/v1/memories/{memory_id}",
        json={
            "expected_version_number": 1,
            "idempotency_key": "audit-2",
            "content_text": "Safe corrected fact",
            "sources": [{"source_type": "trusted_request", "source_resource_id": "r2"}],
        },
    )
    assert corrected.status_code == 200
    assert corrected.json()["version_number"] == 2

    mutations = await client.get(f"/api/v1/memories/{memory_id}/mutations")
    assert mutations.status_code == 200
    operations = [item["operation"] for item in mutations.json()["items"]]
    assert operations == ["create", "correct"]
    assert mutations.json()["items"][0]["after_version_number"] == 1
    assert mutations.json()["items"][1]["after_version_number"] == 2

    deleted = await client.delete(f"/api/v1/memories/{memory_id}")
    assert deleted.status_code == 200
    assert deleted.json()["status"] == "deleted"
    mutations_after = await client.get(f"/api/v1/memories/{memory_id}/mutations")
    assert mutations_after.json()["items"][-1]["operation"] == "delete"
