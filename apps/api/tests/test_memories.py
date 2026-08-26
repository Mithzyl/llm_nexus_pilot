"""Memory Store HTTP contract, lifecycle, ownership, and erasure tests."""

from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from nexuspilot_api.core.errors import ResourceConflictError
from nexuspilot_api.features.memory.schemas.memories import MemoryCreate, MemoryUpdate
from nexuspilot_api.features.memory.services.memory_service import update_memory
from nexuspilot_api.models import (
    LlmMemory,
    LlmMemorySearchTerm,
    LlmMemoryVersion,
    LlmModelToolCall,
    LlmTask,
)


async def create_memory_scope(client: httpx.AsyncClient, suffix: str) -> dict[str, str]:
    """Create one user, Session, Run, Task, and Message usable as Memory evidence."""

    user_id = f"memory-user-{suffix}"
    assert (
        await client.post(
            "/api/v1/users",
            json={"user_id": user_id, "display_name": user_id},
        )
    ).status_code == 201
    session = (
        await client.post(
            "/api/v1/sessions",
            json={"user_id": user_id, "title": suffix},
        )
    ).json()
    run = (
        await client.post(
            "/api/v1/runs",
            json={
                "user_id": user_id,
                "session_id": session["session_id"],
                "user_request": f"Memory request {suffix}",
            },
        )
    ).json()
    task = (
        await client.post(
            f"/api/v1/runs/{run['run_id']}/tasks",
            json={
                "task_type": "memory_test",
                "title": suffix,
                "objective": "Verify Memory scope",
            },
        )
    ).json()
    message = (
        await client.post(
            f"/api/v1/sessions/{session['session_id']}/messages",
            json={
                "role": "user",
                "content_text": "I prefer concise Chinese answers.",
                "run_id": run["run_id"],
            },
        )
    ).json()
    return {
        "user_id": user_id,
        "session_id": session["session_id"],
        "run_id": run["run_id"],
        "task_id": task["task_id"],
        "message_id": message["message_id"],
    }


def memory_payload(scope: dict[str, str], **overrides: object) -> dict[str, object]:
    """Build one valid sourced candidate-memory request for API tests."""

    payload: dict[str, object] = {
        "user_id": scope["user_id"],
        "session_id": scope["session_id"],
        "run_id": scope["run_id"],
        "memory_type": "user_preference",
        "content_text": "The user prefers concise Chinese answers.",
        "importance": "0.80",
        "confidence": "0.95",
        "status": "candidate",
        "semantic_key": "response.language_style",
        "idempotency_key": "create-memory-1",
        "created_by_type": "trusted_caller",
        "sources": [
            {"source_type": "message", "source_resource_id": scope["message_id"]}
        ],
    }
    payload.update(overrides)
    return payload


def test_memory_expiration_is_normalized_to_utc() -> None:
    """Verify offset-aware expiration input becomes one unambiguous UTC instant."""

    payload = MemoryCreate.model_validate(
        {
            "user_id": "timezone-user",
            "memory_type": "working_context",
            "session_id": "timezone-session",
            "content_text": "temporary context",
            "expires_at": "2030-01-01T08:00:00+08:00",
            "idempotency_key": "timezone-memory",
            "sources": [
                {
                    "source_type": "trusted_request",
                    "source_resource_id": "timezone-request",
                }
            ],
        }
    )

    assert payload.expires_at is not None
    assert payload.expires_at.utcoffset() == datetime(2030, 1, 1, tzinfo=UTC).utcoffset()
    assert payload.expires_at.hour == 0


async def test_memory_create_is_idempotent_and_rejects_changed_request(
    client: httpx.AsyncClient,
) -> None:
    """Verify retries return one Memory while key reuse for different content is rejected."""

    scope = await create_memory_scope(client, "idempotency")
    payload = memory_payload(scope)

    first = await client.post("/api/v1/memories", json=payload)
    replay = await client.post("/api/v1/memories", json=payload)
    changed = await client.post(
        "/api/v1/memories",
        json={**payload, "content_text": "A different fact."},
    )

    assert first.status_code == 201
    assert replay.status_code == 200
    assert replay.json()["memory_id"] == first.json()["memory_id"]
    assert replay.json()["version_number"] == 1
    assert changed.status_code == 409
    assert changed.json() == {"detail": "Memory idempotency key was reused with another request"}


async def test_memory_scope_and_model_activation_are_validated(
    client: httpx.AsyncClient,
) -> None:
    """Verify cross-owner evidence and model-produced active facts are rejected."""

    first_scope = await create_memory_scope(client, "scope-a")
    second_scope = await create_memory_scope(client, "scope-b")

    cross_owner = await client.post(
        "/api/v1/memories",
        json=memory_payload(
            first_scope,
            sources=[
                {
                    "source_type": "message",
                    "source_resource_id": second_scope["message_id"],
                }
            ],
        ),
    )
    model_active = await client.post(
        "/api/v1/memories",
        json=memory_payload(
            first_scope,
            idempotency_key="model-active",
            status="active",
            created_by_type="model_attempt",
            created_by_attempt_id="missing-attempt",
        ),
    )

    assert cross_owner.status_code == 409
    assert cross_owner.json() == {"detail": "Memory source does not belong to the user"}
    assert model_active.status_code == 422


async def test_memory_scope_rejects_cross_session_and_cross_run_relations(
    client: httpx.AsyncClient,
) -> None:
    """Verify explicit nested identifiers must describe one real ownership chain."""

    scope = await create_memory_scope(client, "scope-relations")
    second_session = (
        await client.post(
            "/api/v1/sessions",
            json={"user_id": scope["user_id"], "title": "Second session"},
        )
    ).json()
    second_run = (
        await client.post(
            "/api/v1/runs",
            json={
                "user_id": scope["user_id"],
                "session_id": second_session["session_id"],
                "user_request": "Second run",
            },
        )
    ).json()
    second_task = (
        await client.post(
            f"/api/v1/runs/{second_run['run_id']}/tasks",
            json={
                "task_type": "memory_test",
                "title": "Second task",
                "objective": "Verify cross-run rejection",
            },
        )
    ).json()

    cross_session = await client.post(
        "/api/v1/memories",
        json=memory_payload(
            scope,
            run_id=second_run["run_id"],
            idempotency_key="cross-session-scope",
        ),
    )
    cross_run = await client.post(
        "/api/v1/memories",
        json=memory_payload(
            scope,
            task_id=second_task["task_id"],
            idempotency_key="cross-run-scope",
        ),
    )

    assert cross_session.status_code == 409
    assert cross_session.json() == {"detail": "Memory run does not belong to the session"}
    assert cross_run.status_code == 409
    assert cross_run.json() == {"detail": "Memory task does not belong to the run"}


async def test_scoped_sources_and_model_creator_attempt_cannot_be_bypassed(
    client: httpx.AsyncClient,
) -> None:
    """Verify inherited scope and model provenance require matching typed source evidence."""

    scope = await create_memory_scope(client, "strict-source")
    artifact_response = await client.post(
        f"/api/v1/runs/{scope['run_id']}/artifacts",
        data={"artifact_type": "memory_source", "task_id": scope["task_id"]},
        files={"file": ("source.txt", b"task-scoped evidence", "text/plain")},
    )
    assert artifact_response.status_code == 201
    artifact = artifact_response.json()
    sibling_task_response = await client.post(
        f"/api/v1/runs/{scope['run_id']}/tasks",
        json={
            "task_type": "memory_test",
            "title": "Sibling source task",
            "objective": "Provide evidence outside the requested Task scope",
        },
    )
    assert sibling_task_response.status_code == 201
    sibling_artifact_response = await client.post(
        f"/api/v1/runs/{scope['run_id']}/artifacts",
        data={
            "artifact_type": "memory_source",
            "task_id": sibling_task_response.json()["task_id"],
        },
        files={"file": ("sibling.txt", b"sibling evidence", "text/plain")},
    )
    assert sibling_artifact_response.status_code == 201
    inherited = await client.post(
        "/api/v1/memories",
        json=memory_payload(
            scope,
            session_id=None,
            run_id=None,
            task_id=scope["task_id"],
            idempotency_key="task-inheritance",
            status="active",
            sources=[
                {
                    "source_type": "artifact",
                    "source_resource_id": artifact["artifact_id"],
                }
            ],
        ),
    )
    trusted_bypass = await client.post(
        "/api/v1/memories",
        json=memory_payload(
            scope,
            idempotency_key="trusted-bypass",
            sources=[
                {
                    "source_type": "trusted_request",
                    "source_resource_id": "caller-claim",
                }
            ],
        ),
    )
    attempt_response = await client.post(
        f"/api/v1/runs/{scope['run_id']}/attempts",
        json={
            "task_id": scope["task_id"],
            "provider": "openai",
            "model": "test-model",
            "status": "completed",
        },
    )
    assert attempt_response.status_code == 201
    missing_attempt_source = await client.post(
        "/api/v1/memories",
        json=memory_payload(
            scope,
            idempotency_key="model-without-attempt-source",
            created_by_type="model_attempt",
            created_by_attempt_id=attempt_response.json()["attempt_id"],
        ),
    )
    valid_model_candidate = await client.post(
        "/api/v1/memories",
        json=memory_payload(
            scope,
            idempotency_key="model-with-attempt-source",
            created_by_type="model_attempt",
            created_by_attempt_id=attempt_response.json()["attempt_id"],
            sources=[
                {
                    "source_type": "model_attempt",
                    "source_resource_id": attempt_response.json()["attempt_id"],
                }
            ],
        ),
    )
    sibling_scope_source = await client.post(
        "/api/v1/memories",
        json=memory_payload(
            scope,
            task_id=scope["task_id"],
            idempotency_key="sibling-task-source",
            sources=[
                {
                    "source_type": "artifact",
                    "source_resource_id": sibling_artifact_response.json()["artifact_id"],
                }
            ],
        ),
    )
    inherited_list = await client.get(
        "/api/v1/memories",
        params={"user_id": scope["user_id"], "task_id": scope["task_id"]},
    )
    inherited_retrieval = await client.post(
        "/api/v1/memory-retrievals",
        json={
            "user_id": scope["user_id"],
            "task_id": scope["task_id"],
            "query_text": "concise Chinese",
            "limit": 10,
            "token_budget": 200,
            "idempotency_key": "task-scope-retrieval",
        },
    )

    assert inherited.status_code == 201
    assert inherited.json()["session_id"] == scope["session_id"]
    assert inherited.json()["run_id"] == scope["run_id"]
    assert inherited.json()["task_id"] == scope["task_id"]
    assert [item["memory_id"] for item in inherited_list.json()["items"]] == [
        inherited.json()["memory_id"]
    ]
    assert [item["memory_id"] for item in inherited_retrieval.json()["results"]] == [
        inherited.json()["memory_id"]
    ]
    assert trusted_bypass.status_code == 409
    assert missing_attempt_source.status_code == 422
    assert missing_attempt_source.json() == {
        "detail": "Model-created Memory requires its model attempt as a source"
    }
    assert valid_model_candidate.status_code == 201
    assert valid_model_candidate.json()["status"] == "candidate"
    assert sibling_scope_source.status_code == 409
    assert sibling_scope_source.json() == {
        "detail": "Memory source does not belong to the Memory scope"
    }


async def test_memory_accepts_valid_tool_call_source(
    client: httpx.AsyncClient,
    test_database_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Verify a task-scoped tool call can serve as validated Memory evidence."""

    scope = await create_memory_scope(client, "tool-call-source")
    attempt_response = await client.post(
        f"/api/v1/runs/{scope['run_id']}/attempts",
        json={
            "task_id": scope["task_id"],
            "provider": "openai",
            "model": "test-model",
            "status": "completed",
        },
    )
    assert attempt_response.status_code == 201
    tool_call_id = "memory-tool-call-source"
    async with test_database_session_factory() as db_session:
        db_session.add(
            LlmModelToolCall(
                tool_call_id=tool_call_id,
                attempt_id=attempt_response.json()["attempt_id"],
                tool_name="read_file",
                risk_level="read",
                input_json={"path": "/workspace/README.md"},
                status="completed",
                permission_decision="allowed",
            )
        )
        await db_session.commit()

    response = await client.post(
        "/api/v1/memories",
        json=memory_payload(
            scope,
            task_id=scope["task_id"],
            idempotency_key="valid-tool-call-source",
            sources=[
                {
                    "source_type": "tool_call",
                    "source_resource_id": tool_call_id,
                }
            ],
        ),
    )

    assert response.status_code == 201
    assert response.json()["task_id"] == scope["task_id"]
    assert response.json()["sources"][0]["source_type"] == "tool_call"


@pytest.mark.parametrize(
    ("memory_type", "extra_fields"),
    [
        ("working_context", {"expires_at": "2099-01-01T00:00:00Z"}),
        ("session_episode", {}),
        ("user_fact", {}),
        ("user_preference", {}),
        ("execution_lesson", {}),
    ],
)
async def test_memory_accepts_each_supported_type_with_valid_scope(
    client: httpx.AsyncClient,
    memory_type: str,
    extra_fields: dict[str, object],
) -> None:
    """Verify every public Memory type can be created with its required scope."""

    scope = await create_memory_scope(client, f"valid-type-{memory_type}")
    response = await client.post(
        "/api/v1/memories",
        json=memory_payload(
            scope,
            memory_type=memory_type,
            idempotency_key=f"valid-type-{memory_type}",
            **extra_fields,
        ),
    )

    assert response.status_code == 201
    assert response.json()["memory_type"] == memory_type


async def test_memory_scope_parent_deletion_is_restricted(
    client: httpx.AsyncClient,
    test_database_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Verify deleting a parent cannot silently widen a task-scoped Memory fact."""

    scope = await create_memory_scope(client, "parent-delete")
    artifact_response = await client.post(
        f"/api/v1/runs/{scope['run_id']}/artifacts",
        data={"artifact_type": "memory_source", "task_id": scope["task_id"]},
        files={"file": ("source.txt", b"task evidence", "text/plain")},
    )
    assert artifact_response.status_code == 201
    memory_response = await client.post(
        "/api/v1/memories",
        json=memory_payload(
            scope,
            task_id=scope["task_id"],
            status="active",
            idempotency_key="parent-delete-memory",
            sources=[
                {
                    "source_type": "artifact",
                    "source_resource_id": artifact_response.json()["artifact_id"],
                }
            ],
        ),
    )
    assert memory_response.status_code == 201

    async with test_database_session_factory() as db_session:
        with pytest.raises(IntegrityError):
            await db_session.execute(
                delete(LlmTask).where(LlmTask.task_id == scope["task_id"])
            )
            await db_session.commit()
        await db_session.rollback()
        memory = await db_session.get(LlmMemory, memory_response.json()["memory_id"])

    assert memory is not None
    assert memory.task_id == scope["task_id"]


async def test_memory_activation_correction_and_delete_preserve_auditable_versions(
    client: httpx.AsyncClient,
    test_database_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Verify optimistic updates version content and deletion erases text and search terms."""

    scope = await create_memory_scope(client, "lifecycle")
    created = (
        await client.post("/api/v1/memories", json=memory_payload(scope))
    ).json()

    missing_version = await client.patch(
        f"/api/v1/memories/{created['memory_id']}",
        json={"status": "active", "idempotency_key": "activate-without-version"},
    )
    activated = await client.patch(
        f"/api/v1/memories/{created['memory_id']}",
        json={
            "expected_version_number": 1,
            "status": "active",
            "idempotency_key": "activate-memory",
        },
    )
    stale = await client.patch(
        f"/api/v1/memories/{created['memory_id']}",
        json={
            "expected_version_number": 2,
            "content_text": "Stale correction",
            "idempotency_key": "stale-correction",
            "sources": [
                {"source_type": "message", "source_resource_id": scope["message_id"]}
            ],
        },
    )
    corrected = await client.patch(
        f"/api/v1/memories/{created['memory_id']}",
        json={
            "expected_version_number": 1,
            "content_text": "The user prefers brief answers written in Chinese.",
            "confidence": "0.99",
            "idempotency_key": "correct-memory",
            "sources": [
                {"source_type": "message", "source_resource_id": scope["message_id"]}
            ],
        },
    )
    versions = await client.get(f"/api/v1/memories/{created['memory_id']}/versions")
    replayed_activation = await client.patch(
        f"/api/v1/memories/{created['memory_id']}",
        json={
            "expected_version_number": 1,
            "status": "active",
            "idempotency_key": "activate-memory",
        },
    )
    changed_activation_key = await client.patch(
        f"/api/v1/memories/{created['memory_id']}",
        json={
            "expected_version_number": 2,
            "status": "rejected",
            "idempotency_key": "activate-memory",
        },
    )
    deleted = await client.delete(f"/api/v1/memories/{created['memory_id']}")
    deleted_again = await client.delete(f"/api/v1/memories/{created['memory_id']}")

    assert missing_version.status_code == 422
    assert activated.status_code == 200
    assert activated.json()["status"] == "active"
    assert stale.status_code == 409
    assert corrected.status_code == 200
    assert corrected.json()["version_number"] == 2
    assert [item["version_number"] for item in versions.json()["items"]] == [1, 2]
    assert replayed_activation.status_code == 200
    assert replayed_activation.json()["version_number"] == 2
    assert changed_activation_key.status_code == 409
    assert changed_activation_key.json() == {
        "detail": "Memory mutation idempotency key was reused with another request"
    }
    assert deleted.status_code == 200
    assert deleted.json()["status"] == "deleted"
    assert deleted.json()["content_text"] is None
    assert deleted_again.status_code == 200
    assert deleted_again.json()["deleted_at"] == deleted.json()["deleted_at"]

    async with test_database_session_factory() as db_session:
        remaining_text_count = await db_session.scalar(
            select(func.count())
            .select_from(LlmMemoryVersion)
            .where(
                LlmMemoryVersion.memory_id == created["memory_id"],
                LlmMemoryVersion.content_text.is_not(None),
            )
        )
        search_term_count = await db_session.scalar(
            select(func.count())
            .select_from(LlmMemorySearchTerm)
            .join(
                LlmMemoryVersion,
                LlmMemoryVersion.memory_version_id
                == LlmMemorySearchTerm.memory_version_id,
            )
            .where(LlmMemoryVersion.memory_id == created["memory_id"])
        )
    assert remaining_text_count == 0
    assert search_term_count == 0


async def test_memory_correction_preserves_zero_importance_and_confidence(
    client: httpx.AsyncClient,
) -> None:
    """Verify explicit Decimal zero values are not replaced by the prior version."""

    scope = await create_memory_scope(client, "zero-values")
    created = (
        await client.post("/api/v1/memories", json=memory_payload(scope))
    ).json()

    corrected = await client.patch(
        f"/api/v1/memories/{created['memory_id']}",
        json={
            "expected_version_number": 1,
            "importance": "0",
            "confidence": "0",
            "idempotency_key": "zero-correction",
            "sources": [
                {"source_type": "message", "source_resource_id": scope["message_id"]}
            ],
        },
    )

    assert corrected.status_code == 200
    assert corrected.json()["importance"] == "0.0000"
    assert corrected.json()["confidence"] == "0.0000"


async def test_memory_mutation_idempotency_distinguishes_omitted_and_explicit_null(
    client: httpx.AsyncClient,
) -> None:
    """Verify request hashes preserve PATCH semantics for omitted and cleared fields."""

    scope = await create_memory_scope(client, "nullable-idempotency")
    created = (
        await client.post("/api/v1/memories", json=memory_payload(scope))
    ).json()
    first = await client.patch(
        f"/api/v1/memories/{created['memory_id']}",
        json={
            "expected_version_number": 1,
            "status": "active",
            "idempotency_key": "nullable-mutation",
        },
    )
    changed_semantics = await client.patch(
        f"/api/v1/memories/{created['memory_id']}",
        json={
            "expected_version_number": 1,
            "status": "active",
            "semantic_key": None,
            "idempotency_key": "nullable-mutation",
        },
    )

    assert first.status_code == 200
    assert changed_semantics.status_code == 409
    assert changed_semantics.json() == {
        "detail": "Memory mutation idempotency key was reused with another request"
    }


async def test_memory_update_rejects_null_version_and_state_values(
    client: httpx.AsyncClient,
) -> None:
    """Verify explicit null cannot create an unchanged version or no-op state mutation."""

    scope = await create_memory_scope(client, "null-update-fields")
    created = (
        await client.post("/api/v1/memories", json=memory_payload(scope))
    ).json()

    for index, field_name in enumerate(
        ["content_text", "importance", "confidence", "status", "supersedes_memory_id"]
    ):
        response = await client.patch(
            f"/api/v1/memories/{created['memory_id']}",
            json={
                "expected_version_number": 1,
                "idempotency_key": f"null-field-{index}",
                field_name: None,
                **(
                    {
                        "sources": [
                            {
                                "source_type": "message",
                                "source_resource_id": scope["message_id"],
                            }
                        ]
                    }
                    if field_name in {"content_text", "importance", "confidence"}
                    else {}
                ),
            },
        )

        assert response.status_code == 422, field_name


async def test_memory_update_rejects_sources_without_a_new_version(
    client: httpx.AsyncClient,
) -> None:
    """Verify state-only updates cannot accept evidence that would be silently discarded."""

    scope = await create_memory_scope(client, "unused-update-source")
    created = (
        await client.post("/api/v1/memories", json=memory_payload(scope))
    ).json()

    response = await client.patch(
        f"/api/v1/memories/{created['memory_id']}",
        json={
            "expected_version_number": 1,
            "status": "active",
            "idempotency_key": "unused-update-source",
            "sources": [
                {"source_type": "message", "source_resource_id": scope["message_id"]}
            ],
        },
    )

    assert response.status_code == 422


async def test_memory_update_converts_mysql_deadlock_to_conflict(
    client: httpx.AsyncClient,
    test_database_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify a MySQL deadlock never escapes the Memory API as an internal failure."""

    scope = await create_memory_scope(client, "update-deadlock")
    created = (
        await client.post("/api/v1/memories", json=memory_payload(scope))
    ).json()

    async with test_database_session_factory() as db_session:
        async def raise_deadlock() -> None:
            """Simulate MySQL error 1213 at the transaction commit boundary."""

            raise OperationalError("COMMIT", {}, Exception(1213, "deadlock"))

        monkeypatch.setattr(db_session, "commit", raise_deadlock)
        with pytest.raises(
            ResourceConflictError,
            match="Memory update conflicted with a concurrent request",
        ):
            await update_memory(
                db_session,
                created["memory_id"],
                MemoryUpdate(
                    expected_version_number=1,
                    status="active",
                    idempotency_key="deadlocked-activation",
                ),
            )

    detail = await client.get(f"/api/v1/memories/{created['memory_id']}")
    assert detail.json()["status"] == "candidate"


@pytest.mark.parametrize(
    "invalid_fields",
    [
        {"content_text": " "},
        {"content_text": "x" * 4_001},
        {"sources": []},
        {
            "sources": [
                {
                    "source_type": "trusted_request",
                    "source_resource_id": f"source-{index}",
                }
                for index in range(21)
            ]
        },
        {"importance": "1.01"},
        {"expires_at": "2030-01-01T00:00:00"},
        {"unknown_memory_option": True},
    ],
)
async def test_memory_create_rejects_schema_and_resource_limit_violations(
    client: httpx.AsyncClient,
    invalid_fields: dict[str, object],
) -> None:
    """Verify invalid text, counts, ranges, timestamps, and unknown fields fail early."""

    scope = await create_memory_scope(
        client,
        f"schema-boundary-{abs(hash(repr(invalid_fields)))}",
    )
    response = await client.post(
        "/api/v1/memories",
        json=memory_payload(scope, **invalid_fields),
    )

    assert response.status_code == 422


async def test_memory_create_accepts_exact_text_and_source_limits(
    client: httpx.AsyncClient,
) -> None:
    """Verify the documented 4000-character and 20-source boundaries remain usable."""

    scope = await create_memory_scope(client, "maximum-boundaries")
    response = await client.post(
        "/api/v1/memories",
        json=memory_payload(
            scope,
            session_id=None,
            run_id=None,
            task_id=None,
            memory_type="user_fact",
            content_text="x" * 4_000,
            idempotency_key="maximum-boundaries",
            sources=[
                {
                    "source_type": "trusted_request",
                    "source_resource_id": f"maximum-source-{index}",
                }
                for index in range(20)
            ],
        ),
    )

    assert response.status_code == 201
    assert len(response.json()["content_text"]) == 4_000
    assert len(response.json()["sources"]) == 20


async def test_memory_sources_reject_missing_and_duplicate_evidence(
    client: httpx.AsyncClient,
) -> None:
    """Verify every source locator exists and duplicate evidence is rejected."""

    scope = await create_memory_scope(client, "invalid-sources")
    missing = await client.post(
        "/api/v1/memories",
        json=memory_payload(
            scope,
            idempotency_key="missing-source",
            sources=[{"source_type": "message", "source_resource_id": "missing-message"}],
        ),
    )
    duplicate_source = {
        "source_type": "message",
        "source_resource_id": scope["message_id"],
    }
    duplicate = await client.post(
        "/api/v1/memories",
        json=memory_payload(
            scope,
            idempotency_key="duplicate-source",
            sources=[duplicate_source, duplicate_source],
        ),
    )

    assert missing.status_code == 404
    assert duplicate.status_code == 422
    assert duplicate.json() == {"detail": "Memory sources must not contain duplicates"}


@pytest.mark.parametrize(
    ("memory_type", "scope_fields", "extra_fields"),
    [
        ("working_context", {}, {}),
        ("working_context", {"session_id": "provided"}, {}),
        ("session_episode", {}, {}),
        ("execution_lesson", {}, {}),
    ],
)
async def test_memory_type_requires_its_minimum_scope_and_expiration(
    client: httpx.AsyncClient,
    memory_type: str,
    scope_fields: dict[str, str],
    extra_fields: dict[str, object],
) -> None:
    """Verify scope-sensitive Memory types cannot be stored as unbounded user facts."""

    scope = await create_memory_scope(client, f"type-scope-{memory_type}-{len(scope_fields)}")
    supplied_scope = {
        "session_id": None,
        "run_id": None,
        "task_id": None,
    }
    if "session_id" in scope_fields:
        supplied_scope["session_id"] = scope["session_id"]
    response = await client.post(
        "/api/v1/memories",
        json=memory_payload(
            scope,
            memory_type=memory_type,
            idempotency_key=f"invalid-type-scope-{memory_type}-{len(scope_fields)}",
            **supplied_scope,
            **extra_fields,
        ),
    )

    assert response.status_code == 422


async def test_active_semantic_fact_requires_explicit_supersession(
    client: httpx.AsyncClient,
) -> None:
    """Verify conflicting active facts cannot coexist or be replaced implicitly."""

    scope = await create_memory_scope(client, "supersession")
    original = (
        await client.post(
            "/api/v1/memories",
            json=memory_payload(
                scope,
                status="active",
                content_text="The user prefers blue.",
                idempotency_key="original-color",
                semantic_key="profile.favorite_color",
            ),
        )
    ).json()
    implicit = await client.post(
        "/api/v1/memories",
        json=memory_payload(
            scope,
            status="active",
            content_text="The user prefers green.",
            idempotency_key="implicit-color",
            semantic_key="profile.favorite_color",
        ),
    )
    replacement = await client.post(
        "/api/v1/memories",
        json=memory_payload(
            scope,
            status="active",
            content_text="The user prefers green.",
            idempotency_key="replacement-color",
            semantic_key="profile.favorite_color",
            supersedes_memory_id=original["memory_id"],
        ),
    )
    original_detail = await client.get(f"/api/v1/memories/{original['memory_id']}")

    assert implicit.status_code == 409
    assert implicit.json() == {"detail": "Active Memory semantic key conflict"}
    assert replacement.status_code == 201
    assert replacement.json()["supersedes_memory_id"] == original["memory_id"]
    assert original_detail.json()["status"] == "superseded"


async def test_rejected_and_superseded_memories_can_only_transition_to_deleted(
    client: httpx.AsyncClient,
) -> None:
    """Verify terminal facts cannot revive but remain eligible for privacy deletion."""

    scope = await create_memory_scope(client, "terminal-delete")
    rejected = (
        await client.post(
            "/api/v1/memories",
            json=memory_payload(
                scope,
                idempotency_key="terminal-rejected",
                semantic_key="terminal.rejected",
            ),
        )
    ).json()
    reject_response = await client.patch(
        f"/api/v1/memories/{rejected['memory_id']}",
        json={
            "expected_version_number": 1,
            "status": "rejected",
            "idempotency_key": "reject-terminal-memory",
        },
    )
    revive_response = await client.patch(
        f"/api/v1/memories/{rejected['memory_id']}",
        json={
            "expected_version_number": 1,
            "status": "active",
            "idempotency_key": "revive-terminal-memory",
        },
    )
    rejected_delete = await client.delete(f"/api/v1/memories/{rejected['memory_id']}")

    original = (
        await client.post(
            "/api/v1/memories",
            json=memory_payload(
                scope,
                status="active",
                content_text="Original terminal fact",
                idempotency_key="terminal-original",
                semantic_key="terminal.superseded",
            ),
        )
    ).json()
    replacement = await client.post(
        "/api/v1/memories",
        json=memory_payload(
            scope,
            status="active",
            content_text="Replacement terminal fact",
            idempotency_key="terminal-replacement",
            semantic_key="terminal.superseded",
            supersedes_memory_id=original["memory_id"],
        ),
    )
    superseded_delete = await client.delete(f"/api/v1/memories/{original['memory_id']}")

    assert reject_response.status_code == 200
    assert revive_response.status_code == 409
    assert rejected_delete.json()["status"] == "deleted"
    assert replacement.status_code == 201
    assert superseded_delete.json()["status"] == "deleted"


async def test_memory_list_is_owner_scoped_and_cursor_bound(
    client: httpx.AsyncClient,
) -> None:
    """Verify Memory pages require an owner and reject cursor replay under new filters."""

    scope = await create_memory_scope(client, "pagination")
    for index in range(3):
        response = await client.post(
            "/api/v1/memories",
            json=memory_payload(
                scope,
                content_text=f"Preference number {index}",
                semantic_key=f"preference.{index}",
                idempotency_key=f"create-{index}",
            ),
        )
        assert response.status_code == 201

    missing_owner = await client.get("/api/v1/memories")
    first_page = (
        await client.get(
            "/api/v1/memories",
            params={"user_id": scope["user_id"], "limit": 2},
        )
    ).json()
    changed_filter = await client.get(
        "/api/v1/memories",
        params={
            "user_id": scope["user_id"],
            "status": "active",
            "cursor": first_page["next_cursor"],
        },
    )

    assert missing_owner.status_code == 422
    assert len(first_page["items"]) == 2
    assert changed_filter.status_code == 422
