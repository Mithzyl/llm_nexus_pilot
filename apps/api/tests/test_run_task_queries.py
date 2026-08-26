"""Run and Task cursor-query and lifecycle-action behavior tests."""

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from nexuspilot_api.models import (
    LlmOutboxEvent,
    LlmRun,
    LlmTask,
    RunStatus,
    TaskStatus,
)


async def create_run_context(client: httpx.AsyncClient, suffix: str) -> tuple[dict, dict]:
    """Create and return one owning conversation and pending run through HTTP."""

    user_id = f"query-user-{suffix}"
    user_response = await client.post(
        "/api/v1/users",
        json={"user_id": user_id, "display_name": user_id},
    )
    assert user_response.status_code == 201
    session_response = await client.post(
        "/api/v1/sessions",
        json={"user_id": user_id, "title": f"Session {suffix}"},
    )
    assert session_response.status_code == 201
    conversation = session_response.json()
    run_response = await client.post(
        "/api/v1/runs",
        json={
            "user_id": user_id,
            "session_id": conversation["session_id"],
            "user_request": f"Request {suffix} " + "x" * 300,
        },
    )
    assert run_response.status_code == 201
    return conversation, run_response.json()


async def create_task(
    client: httpx.AsyncClient,
    run_id: str,
    suffix: str,
    *,
    task_type: str = "research",
    assigned_role: str | None = None,
    max_attempts: int = 3,
) -> dict:
    """Create and return one task through the public HTTP contract."""

    response = await client.post(
        f"/api/v1/runs/{run_id}/tasks",
        json={
            "task_type": task_type,
            "title": f"Task {suffix}",
            "objective": f"Objective {suffix} " + "y" * 300,
            "assigned_role": assigned_role,
            "max_attempts": max_attempts,
        },
    )
    assert response.status_code == 201
    return response.json()


async def set_run_status(
    database_session_factory: async_sessionmaker[AsyncSession],
    run_id: str,
    run_status: RunStatus,
) -> None:
    """Set one persisted Run status to arrange a lifecycle-action scenario."""

    async with database_session_factory() as db_session:
        run = await db_session.get(LlmRun, run_id)
        assert run is not None
        run.status = run_status
        await db_session.commit()


async def set_task_status(
    database_session_factory: async_sessionmaker[AsyncSession],
    task_id: str,
    task_status: TaskStatus,
    *,
    current_attempt: int = 0,
) -> None:
    """Set one persisted Task status and attempt number for an action scenario."""

    async with database_session_factory() as db_session:
        task = await db_session.get(LlmTask, task_id)
        assert task is not None
        task.status = task_status
        task.current_attempt = current_attempt
        await db_session.commit()


async def count_task_events(
    database_session_factory: async_sessionmaker[AsyncSession],
    task_id: str,
    event_type: str,
) -> int:
    """Return the number of durable control events for one task and event type."""

    async with database_session_factory() as db_session:
        count = await db_session.scalar(
            select(func.count())
            .select_from(LlmOutboxEvent)
            .where(
                LlmOutboxEvent.aggregate_id == task_id,
                LlmOutboxEvent.event_type == event_type,
            )
        )
        return int(count or 0)


async def test_run_list_is_bounded_filtered_and_query_cursor_scoped(
    client: httpx.AsyncClient,
) -> None:
    """Verify Run pages omit full requests and reject cursors under changed filters."""

    conversation, first_run = await create_run_context(client, "run-list")
    created_runs = [first_run]
    for suffix in ["two", "three"]:
        response = await client.post(
            "/api/v1/runs",
            json={
                "user_id": first_run["user_id"],
                "session_id": conversation["session_id"],
                "user_request": f"Request {suffix}",
            },
        )
        assert response.status_code == 201
        created_runs.append(response.json())

    first_page = (
        await client.get(
            "/api/v1/runs",
            params={"session_id": conversation["session_id"], "limit": 2},
        )
    ).json()
    second_page = (
        await client.get(
            "/api/v1/runs",
            params={
                "session_id": conversation["session_id"],
                "limit": 2,
                "cursor": first_page["next_cursor"],
            },
        )
    ).json()
    changed_filter = await client.get(
        "/api/v1/runs",
        params={
            "session_id": conversation["session_id"],
            "status": "running",
            "cursor": first_page["next_cursor"],
        },
    )

    assert [item["run_id"] for item in first_page["items"]] == [
        created_runs[0]["run_id"],
        created_runs[1]["run_id"],
    ]
    assert [item["run_id"] for item in second_page["items"]] == [
        created_runs[2]["run_id"]
    ]
    assert "user_request" not in first_page["items"][0]
    assert len(first_page["items"][0]["request_preview"]) == 200
    assert changed_filter.status_code == 422


async def test_run_list_rejects_invalid_time_ranges(client: httpx.AsyncClient) -> None:
    """Verify Run time filters require timezone and an increasing interval."""

    naive = await client.get(
        "/api/v1/runs",
        params={"created_after": "2026-07-31T10:00:00"},
    )
    reversed_range = await client.get(
        "/api/v1/runs",
        params={
            "created_after": "2026-07-31T11:00:00+00:00",
            "created_before": "2026-07-31T10:00:00+00:00",
        },
    )

    assert naive.status_code == 422
    assert reversed_range.status_code == 422


async def test_task_lists_share_filters_and_query_scoped_cursors(
    client: httpx.AsyncClient,
) -> None:
    """Verify global and Run-scoped Task lists share bounded filtering semantics."""

    _, run = await create_run_context(client, "task-list")
    tasks = [
        await create_task(client, run["run_id"], "one", assigned_role="researcher"),
        await create_task(client, run["run_id"], "two", assigned_role="researcher"),
        await create_task(
            client,
            run["run_id"],
            "three",
            task_type="planning",
            assigned_role="planner",
        ),
    ]

    first_page = (
        await client.get(
            f"/api/v1/runs/{run['run_id']}/tasks",
            params={"assigned_role": "researcher", "limit": 1},
        )
    ).json()
    second_page = (
        await client.get(
            "/api/v1/tasks",
            params={
                "run_id": run["run_id"],
                "assigned_role": "researcher",
                "limit": 1,
                "cursor": first_page["next_cursor"],
            },
        )
    ).json()
    changed_filter = await client.get(
        "/api/v1/tasks",
        params={
            "run_id": run["run_id"],
            "task_type": "planning",
            "cursor": first_page["next_cursor"],
        },
    )

    assert first_page["items"][0]["task_id"] == tasks[0]["task_id"]
    assert second_page["items"][0]["task_id"] == tasks[1]["task_id"]
    assert "objective" not in first_page["items"][0]
    assert len(first_page["items"][0]["objective_preview"]) == 200
    assert changed_filter.status_code == 422


async def test_pending_run_cancel_is_immediate_and_idempotent(
    client: httpx.AsyncClient,
) -> None:
    """Verify cancelling a non-running Run immediately cancels its pending tasks."""

    _, run = await create_run_context(client, "cancel-pending")
    task = await create_task(client, run["run_id"], "pending")

    first = await client.post(f"/api/v1/runs/{run['run_id']}/cancel")
    second = await client.post(f"/api/v1/runs/{run['run_id']}/cancel")
    task_detail = await client.get(f"/api/v1/tasks/{task['task_id']}")

    assert first.status_code == 200
    assert first.json()["status"] == "cancelled"
    assert second.json()["status"] == "cancelled"
    assert task_detail.json()["status"] == "cancelled"


async def test_running_run_cancel_creates_one_durable_task_request(
    client: httpx.AsyncClient,
    test_database_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Verify running work enters cancel-requested with one transactional outbox fact."""

    _, run = await create_run_context(client, "cancel-running")
    task = await create_task(client, run["run_id"], "running")
    await set_run_status(test_database_session_factory, run["run_id"], RunStatus.RUNNING)
    await set_task_status(test_database_session_factory, task["task_id"], TaskStatus.RUNNING)

    first = await client.post(f"/api/v1/runs/{run['run_id']}/cancel")
    second = await client.post(f"/api/v1/runs/{run['run_id']}/cancel")
    event_count = await count_task_events(
        test_database_session_factory,
        task["task_id"],
        "task.cancel_requested",
    )

    assert first.json()["status"] == "cancel_requested"
    assert second.json()["status"] == "cancel_requested"
    assert event_count == 1


async def test_task_cancel_and_retry_enforce_state_and_attempt_limits(
    client: httpx.AsyncClient,
    test_database_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Verify Task actions reject terminal misuse and create one durable retry fact."""

    _, run = await create_run_context(client, "task-actions")
    pending_task = await create_task(client, run["run_id"], "pending")
    running_task = await create_task(client, run["run_id"], "running")
    failed_task = await create_task(client, run["run_id"], "failed", max_attempts=3)
    exhausted_task = await create_task(client, run["run_id"], "exhausted", max_attempts=1)
    await set_task_status(
        test_database_session_factory,
        running_task["task_id"],
        TaskStatus.RUNNING,
        current_attempt=1,
    )
    await set_task_status(
        test_database_session_factory,
        failed_task["task_id"],
        TaskStatus.FAILED,
        current_attempt=1,
    )
    await set_task_status(
        test_database_session_factory,
        exhausted_task["task_id"],
        TaskStatus.FAILED,
        current_attempt=1,
    )

    pending_cancel = await client.post(f"/api/v1/tasks/{pending_task['task_id']}/cancel")
    running_cancel = await client.post(f"/api/v1/tasks/{running_task['task_id']}/cancel")
    failed_cancel = await client.post(f"/api/v1/tasks/{failed_task['task_id']}/cancel")
    retry = await client.post(f"/api/v1/tasks/{failed_task['task_id']}/retry")
    duplicate_retry = await client.post(f"/api/v1/tasks/{failed_task['task_id']}/retry")
    exhausted_retry = await client.post(f"/api/v1/tasks/{exhausted_task['task_id']}/retry")

    assert pending_cancel.json()["status"] == "cancelled"
    assert running_cancel.json()["status"] == "cancel_requested"
    assert failed_cancel.status_code == 409
    assert retry.status_code == 200
    assert retry.json()["status"] == "retry_scheduled"
    assert retry.json()["current_attempt"] == 2
    assert duplicate_retry.status_code == 409
    assert exhausted_retry.status_code == 409
    assert await count_task_events(
        test_database_session_factory,
        failed_task["task_id"],
        "task.retry_requested",
    ) == 1
    async with test_database_session_factory() as db_session:
        retry_event = await db_session.scalar(
            select(LlmOutboxEvent).where(
                LlmOutboxEvent.aggregate_id == failed_task["task_id"],
                LlmOutboxEvent.event_type == "task.retry_requested",
            )
        )
        assert retry_event is not None
        assert retry_event.event_id == retry.json()["event_id"]
        assert retry_event.payload_json["message_id"] == retry_event.event_id
        assert retry_event.payload_json["attempt"] == 2


async def test_cancelled_run_rejects_new_tasks(client: httpx.AsyncClient) -> None:
    """Verify task creation cannot reopen a terminal or cancelling Run implicitly."""

    _, run = await create_run_context(client, "closed-run")
    await client.post(f"/api/v1/runs/{run['run_id']}/cancel")

    response = await client.post(
        f"/api/v1/runs/{run['run_id']}/tasks",
        json={"task_type": "late", "title": "Late", "objective": "Must fail"},
    )

    assert response.status_code == 409
