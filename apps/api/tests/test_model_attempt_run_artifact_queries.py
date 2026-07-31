"""Model invocation, provider transport, and Run Artifact query API tests."""

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from test_api import create_test_run

from nexuspilot_api.models import LlmModelTransportAttempt, LlmRunArtifact


async def _create_task(client: httpx.AsyncClient, run_id: str, title: str = "Inspect") -> dict:
    """Create one reusable Task through the public API and return its response body."""

    response = await client.post(
        f"/api/v1/runs/{run_id}/tasks",
        json={"task_type": "research", "title": title, "objective": "Inspect files"},
    )
    assert response.status_code == 201
    return response.json()


async def _create_attempt(
    client: httpx.AsyncClient,
    run_id: str,
    *,
    task_id: str | None = None,
    provider: str = "openai",
    raw_request_uri: str | None = None,
) -> dict:
    """Create one reusable LLM model invocation through the compatibility endpoint."""

    response = await client.post(
        f"/api/v1/runs/{run_id}/attempts",
        json={
            "task_id": task_id,
            "provider": provider,
            "model": "test-model",
            "status": "completed",
            "input_tokens": 10,
            "output_tokens": 2,
            "estimated_cost": "0.001000",
            "raw_request_uri": raw_request_uri,
        },
    )
    assert response.status_code == 201
    return response.json()


async def _upload_artifact(
    client: httpx.AsyncClient,
    run_id: str,
    filename: str,
    artifact_content: bytes,
    *,
    artifact_type: str = "report",
    task_id: str | None = None,
) -> dict:
    """Upload one reusable Run Artifact and return its compatibility response body."""

    form = {"artifact_type": artifact_type}
    if task_id is not None:
        form["task_id"] = task_id
    response = await client.post(
        f"/api/v1/runs/{run_id}/artifacts",
        data=form,
        files={"file": (filename, artifact_content, "text/plain")},
    )
    assert response.status_code == 201
    return response.json()


async def test_attempt_pages_require_owner_scope_and_bind_cursor_filters(
    client: httpx.AsyncClient,
) -> None:
    """Verify Attempt pages stay bounded, omit raw fields, and reject cursor replay."""

    run = await create_test_run(client)
    model_attempts = [
        await _create_attempt(
            client,
            run["run_id"],
            provider="openai" if index < 2 else "deepseek",
            raw_request_uri="minio://private/raw.json" if index == 0 else None,
        )
        for index in range(3)
    ]

    assert (await client.get("/api/v1/attempts")).status_code == 422
    first_response = await client.get(
        "/api/v1/attempts",
        params={"run_id": run["run_id"], "limit": 2},
    )
    assert first_response.status_code == 200
    first = first_response.json()
    second = (
        await client.get(
            "/api/v1/attempts",
            params={
                "run_id": run["run_id"],
                "limit": 2,
                "cursor": first["next_cursor"],
            },
        )
    ).json()
    returned_model_attempt_ids = {
        model_attempt_summary["attempt_id"]
        for model_attempt_summary in first["items"] + second["items"]
    }
    assert returned_model_attempt_ids == {
        model_attempt["attempt_id"] for model_attempt in model_attempts
    }
    assert all(
        "raw_request_uri" not in model_attempt_summary
        and "retries" not in model_attempt_summary
        for model_attempt_summary in first["items"]
    )

    replay = await client.get(
        "/api/v1/attempts",
        params={
            "run_id": run["run_id"],
            "provider": "openai",
            "cursor": first["next_cursor"],
        },
    )
    assert replay.status_code == 422
    detail = (
        await client.get(f"/api/v1/attempts/{model_attempts[0]['attempt_id']}")
    ).json()
    assert detail["has_raw_request"] is True
    assert "raw_request_uri" not in detail


async def test_attempt_query_rejects_cross_run_task_scope(client: httpx.AsyncClient) -> None:
    """Verify callers cannot combine a Run with a Task belonging to another Run."""

    first_run = await create_test_run(client)
    second_run = await create_test_run(client)
    task = await _create_task(client, first_run["run_id"])
    response = await client.get(
        "/api/v1/attempts",
        params={"run_id": second_run["run_id"], "task_id": task["task_id"]},
    )
    assert response.status_code == 422


async def test_physical_retry_pages_are_attempt_scoped_and_ordered(
    client: httpx.AsyncClient,
    test_database_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Verify provider transport attempts cannot reuse another model cursor."""

    run = await create_test_run(client)
    first_attempt = await _create_attempt(client, run["run_id"])
    second_attempt = await _create_attempt(client, run["run_id"])
    async with test_database_session_factory() as db_session:
        db_session.add_all(
            [
                LlmModelTransportAttempt(
                    attempt_id=first_attempt["attempt_id"],
                    attempt_index=index,
                    status_code=503 if index < 3 else 200,
                    latency_ms=index * 10,
                    error_type="upstream" if index < 3 else None,
                    error_message="x" * 700 if index == 1 else None,
                )
                for index in range(1, 4)
            ]
        )
        await db_session.commit()

    first = (
        await client.get(
            f"/api/v1/attempts/{first_attempt['attempt_id']}/retries",
            params={"limit": 2},
        )
    ).json()
    second = (
        await client.get(
            f"/api/v1/attempts/{first_attempt['attempt_id']}/retries",
            params={"limit": 2, "cursor": first["next_cursor"]},
        )
    ).json()
    assert [
        model_transport_attempt["attempt_index"]
        for model_transport_attempt in first["items"] + second["items"]
    ] == [1, 2, 3]
    assert len(first["items"][0]["error_message_preview"]) == 500

    retry_id = first["items"][0]["retry_id"]
    detail = await client.get(f"/api/v1/attempt-retries/{retry_id}")
    assert detail.status_code == 200
    replay = await client.get(
        f"/api/v1/attempts/{second_attempt['attempt_id']}/retries",
        params={"cursor": first["next_cursor"]},
    )
    assert replay.status_code == 422


async def test_artifact_metadata_pages_and_controlled_content_read(
    client: httpx.AsyncClient,
) -> None:
    """Verify Artifact metadata hides storage URIs while controlled reads preserve bytes."""

    run = await create_test_run(client)
    task = await _create_task(client, run["run_id"])
    run_artifacts = [
        await _upload_artifact(
            client,
            run["run_id"],
            f"result-{index}.txt",
            f"verified-{index}".encode(),
            artifact_type="report" if index < 2 else "diff",
            task_id=task["task_id"],
        )
        for index in range(3)
    ]

    assert (await client.get("/api/v1/artifacts")).status_code == 422
    first = (
        await client.get(
            "/api/v1/artifacts",
            params={"run_id": run["run_id"], "limit": 2},
        )
    ).json()
    second = (
        await client.get(
            "/api/v1/artifacts",
            params={
                "run_id": run["run_id"],
                "limit": 2,
                "cursor": first["next_cursor"],
            },
        )
    ).json()
    assert {
        run_artifact_summary["artifact_id"]
        for run_artifact_summary in first["items"] + second["items"]
    } == {
        run_artifact["artifact_id"] for run_artifact in run_artifacts
    }
    assert all(
        "storage_uri" not in run_artifact_summary
        for run_artifact_summary in first["items"]
    )
    replay = await client.get(
        "/api/v1/artifacts",
        params={
            "run_id": run["run_id"],
            "artifact_type": "report",
            "cursor": first["next_cursor"],
        },
    )
    assert replay.status_code == 422

    run_artifact = run_artifacts[0]
    detail = (
        await client.get(f"/api/v1/artifacts/{run_artifact['artifact_id']}")
    ).json()
    assert detail["content_available"] is True
    assert "storage_uri" not in detail
    artifact_content_response = await client.get(
        f"/api/v1/artifacts/{run_artifact['artifact_id']}/content"
    )
    assert artifact_content_response.status_code == 200
    assert artifact_content_response.content == b"verified-0"
    assert artifact_content_response.headers["content-type"].startswith("text/plain")
    assert artifact_content_response.headers["content-length"] == str(len(b"verified-0"))
    assert artifact_content_response.headers["cache-control"] == "private, no-store"
    assert (
        "filename*=UTF-8''result-0.txt"
        in artifact_content_response.headers["content-disposition"]
    )


async def test_artifact_content_reports_missing_object_as_storage_inconsistency(
    client: httpx.AsyncClient,
    test_database_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Verify metadata whose object is missing returns a controlled upstream error."""

    run = await create_test_run(client)
    run_artifact = await _upload_artifact(client, run["run_id"], "missing.txt", b"content")
    async with test_database_session_factory() as db_session:
        run_artifact_record = await db_session.get(
            LlmRunArtifact,
            run_artifact["artifact_id"],
        )
        assert run_artifact_record is not None
        run_artifact_record.storage_uri = "memory://test/not-present"
        await db_session.commit()

    response = await client.get(f"/api/v1/artifacts/{run_artifact['artifact_id']}/content")
    assert response.status_code == 502
    assert response.json() == {
        "detail": "Artifact content is inconsistent with stored metadata"
    }


async def test_artifact_content_rejects_database_size_mismatch(
    client: httpx.AsyncClient,
    test_database_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Verify content streaming does not start when persisted size metadata is incorrect."""

    run = await create_test_run(client)
    run_artifact = await _upload_artifact(client, run["run_id"], "size.txt", b"content")
    async with test_database_session_factory() as db_session:
        run_artifact_record = await db_session.get(
            LlmRunArtifact,
            run_artifact["artifact_id"],
        )
        assert run_artifact_record is not None
        run_artifact_record.size_bytes += 1
        await db_session.commit()

    response = await client.get(f"/api/v1/artifacts/{run_artifact['artifact_id']}/content")
    assert response.status_code == 502
    assert response.json() == {
        "detail": "Artifact content is inconsistent with stored metadata"
    }
