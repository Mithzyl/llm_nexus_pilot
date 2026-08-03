"""Memory retrieval filtering, ranking, budgeting, and evidence tests."""

from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from test_memories import create_memory_scope, memory_payload

from nexuspilot_api.models import LlmMemory


async def create_active_memory(
    client: httpx.AsyncClient,
    scope: dict[str, str],
    *,
    content_text: str,
    idempotency_key: str,
    semantic_key: str,
    importance: str = "0.50",
    expires_at: str | None = None,
) -> dict:
    """Create an active Memory through the public API for retrieval scenarios."""

    response = await client.post(
        "/api/v1/memories",
        json=memory_payload(
            scope,
            content_text=content_text,
            idempotency_key=idempotency_key,
            semantic_key=semantic_key,
            status="active",
            importance=importance,
            expires_at=expires_at,
        ),
    )
    assert response.status_code == 201
    return response.json()


async def test_memory_retrieval_filters_owner_status_expiry_and_scope(
    client: httpx.AsyncClient,
) -> None:
    """Verify retrieval never returns foreign, candidate, expired, or mismatched-scope facts."""

    scope = await create_memory_scope(client, "retrieve-owner")
    foreign_scope = await create_memory_scope(client, "retrieve-foreign")
    matching = await create_active_memory(
        client,
        scope,
        content_text="用户偏好使用蓝色主题",
        idempotency_key="matching-active",
        semantic_key="theme.active",
    )
    await create_active_memory(
        client,
        scope,
        content_text="用户曾经偏好蓝色主题",
        idempotency_key="expired-active",
        semantic_key="theme.expired",
        expires_at=(datetime.now(UTC) - timedelta(minutes=1)).isoformat(),
    )
    candidate = await client.post(
        "/api/v1/memories",
        json=memory_payload(
            scope,
            content_text="候选蓝色主题",
            idempotency_key="candidate-theme",
            semantic_key="theme.candidate",
        ),
    )
    assert candidate.status_code == 201
    await create_active_memory(
        client,
        foreign_scope,
        content_text="外部用户蓝色主题",
        idempotency_key="foreign-theme",
        semantic_key="theme.foreign",
    )

    response = await client.post(
        "/api/v1/memory-retrievals",
        json={
            "user_id": scope["user_id"],
            "session_id": scope["session_id"],
            "run_id": scope["run_id"],
            "task_id": scope["task_id"],
            "query_text": "蓝色主题偏好",
            "memory_types": ["user_preference"],
            "limit": 20,
            "token_budget": 200,
            "idempotency_key": "retrieve-theme",
        },
    )

    assert response.status_code == 201
    assert [item["memory_id"] for item in response.json()["results"]] == [
        matching["memory_id"]
    ]
    assert response.json()["candidate_method"] == "lexical_hash_v1"
    assert response.json()["ranker_version"] == "memory_ranker_v1"


async def test_memory_retrieval_is_deterministic_budgeted_and_queryable(
    client: httpx.AsyncClient,
) -> None:
    """Verify persisted ranking is stable and selects only whole facts within token budget."""

    scope = await create_memory_scope(client, "ranking")
    preferred = await create_active_memory(
        client,
        scope,
        content_text="Python testing preference",
        idempotency_key="python-preferred",
        semantic_key="language.python",
        importance="0.90",
    )
    await create_active_memory(
        client,
        scope,
        content_text="Python documentation preference with several additional words",
        idempotency_key="python-long",
        semantic_key="language.python.docs",
        importance="0.20",
    )

    payload = {
        "user_id": scope["user_id"],
        "session_id": scope["session_id"],
        "run_id": scope["run_id"],
        "query_text": "Python testing",
        "limit": 2,
        "token_budget": 30,
        "idempotency_key": "rank-python",
    }
    first = await client.post("/api/v1/memory-retrievals", json=payload)
    replay = await client.post("/api/v1/memory-retrievals", json=payload)
    detail = await client.get(
        f"/api/v1/memory-retrievals/{first.json()['memory_retrieval_id']}"
    )

    assert first.status_code == 201
    assert replay.status_code == 200
    assert replay.json() == first.json()
    assert detail.status_code == 200
    assert detail.json() == first.json()
    assert first.json()["results"][0]["memory_id"] == preferred["memory_id"]
    assert first.json()["results"][0]["selected"] is True
    assert any(
        result["exclusion_reason"] == "token_budget_exceeded"
        for result in first.json()["results"][1:]
    )


async def test_memory_retrieval_idempotency_rejects_changed_query(
    client: httpx.AsyncClient,
) -> None:
    """Verify a retrieval key cannot be replayed with different query semantics."""

    scope = await create_memory_scope(client, "retrieval-key")
    payload = {
        "user_id": scope["user_id"],
        "query_text": "first query",
        "limit": 10,
        "token_budget": 100,
        "idempotency_key": "same-retrieval-key",
    }
    assert (
        await client.post("/api/v1/memory-retrievals", json=payload)
    ).status_code == 201

    changed = await client.post(
        "/api/v1/memory-retrievals",
        json={**payload, "query_text": "changed query"},
    )

    assert changed.status_code == 409
    assert changed.json() == {
        "detail": "Memory retrieval idempotency key was reused with another request"
    }


async def test_memory_retrieval_applies_hierarchical_scope_without_leakage(
    client: httpx.AsyncClient,
) -> None:
    """Verify broad retrieval excludes scoped facts while narrow retrieval includes global facts."""

    scope = await create_memory_scope(client, "hierarchical-retrieval")
    scoped_memory = await create_active_memory(
        client,
        scope,
        content_text="Python scoped preference",
        idempotency_key="scoped-python",
        semantic_key="scoped.python",
    )
    global_response = await client.post(
        "/api/v1/memories",
        json=memory_payload(
            scope,
            session_id=None,
            run_id=None,
            task_id=None,
            status="active",
            content_text="Python global preference",
            idempotency_key="global-python",
            semantic_key="global.python",
            sources=[
                {
                    "source_type": "trusted_request",
                    "source_resource_id": "global-python-request",
                }
            ],
        ),
    )
    assert global_response.status_code == 201
    global_memory = global_response.json()

    broad_retrieval = await client.post(
        "/api/v1/memory-retrievals",
        json={
            "user_id": scope["user_id"],
            "query_text": "Python preference",
            "limit": 10,
            "token_budget": 500,
            "idempotency_key": "broad-python-retrieval",
        },
    )
    narrow_retrieval = await client.post(
        "/api/v1/memory-retrievals",
        json={
            "user_id": scope["user_id"],
            "task_id": scope["task_id"],
            "query_text": "Python preference",
            "limit": 10,
            "token_budget": 500,
            "idempotency_key": "narrow-python-retrieval",
        },
    )

    assert broad_retrieval.status_code == 201
    assert [item["memory_id"] for item in broad_retrieval.json()["results"]] == [
        global_memory["memory_id"]
    ]
    assert narrow_retrieval.status_code == 201
    assert {item["memory_id"] for item in narrow_retrieval.json()["results"]} == {
        global_memory["memory_id"],
        scoped_memory["memory_id"],
    }


async def test_memory_retrieval_rejects_query_without_searchable_terms(
    client: httpx.AsyncClient,
) -> None:
    """Verify punctuation-only queries fail instead of recording meaningless retrievals."""

    scope = await create_memory_scope(client, "empty-query-terms")
    response = await client.post(
        "/api/v1/memory-retrievals",
        json={
            "user_id": scope["user_id"],
            "query_text": "... !!!",
            "idempotency_key": "empty-query-terms",
        },
    )

    assert response.status_code == 422
    assert response.json() == {"detail": "Memory retrieval query has no searchable terms"}


async def test_memory_retrieval_uses_memory_id_as_final_tie_break(
    client: httpx.AsyncClient,
    test_database_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Verify equal scores and timestamps have a deterministic Memory ID order."""

    scope = await create_memory_scope(client, "retrieval-tie-break")
    first = await create_active_memory(
        client,
        scope,
        content_text="identical retrieval terms",
        idempotency_key="tie-memory-first",
        semantic_key="tie.first",
    )
    second = await create_active_memory(
        client,
        scope,
        content_text="identical retrieval terms",
        idempotency_key="tie-memory-second",
        semantic_key="tie.second",
    )
    fixed_updated_at = datetime(2030, 1, 1, tzinfo=UTC)
    async with test_database_session_factory() as db_session:
        await db_session.execute(
            update(LlmMemory)
            .where(LlmMemory.memory_id.in_([first["memory_id"], second["memory_id"]]))
            .values(updated_at=fixed_updated_at)
        )
        await db_session.commit()

    response = await client.post(
        "/api/v1/memory-retrievals",
        json={
            "user_id": scope["user_id"],
            "session_id": scope["session_id"],
            "run_id": scope["run_id"],
            "query_text": "identical retrieval terms",
            "limit": 10,
            "token_budget": 500,
            "idempotency_key": "retrieval-tie-break",
        },
    )

    assert response.status_code == 201
    assert [result["memory_id"] for result in response.json()["results"]] == sorted(
        [first["memory_id"], second["memory_id"]]
    )


async def test_deleted_memory_erases_text_from_persisted_retrieval_evidence(
    client: httpx.AsyncClient,
) -> None:
    """Verify historical ranking survives deletion while erased Memory text does not."""

    scope = await create_memory_scope(client, "retrieval-delete")
    memory = await create_active_memory(
        client,
        scope,
        content_text="sensitive historical retrieval evidence",
        idempotency_key="retrieval-delete-memory",
        semantic_key="retrieval.delete",
    )
    retrieval = await client.post(
        "/api/v1/memory-retrievals",
        json={
            "user_id": scope["user_id"],
            "session_id": scope["session_id"],
            "run_id": scope["run_id"],
            "query_text": "historical retrieval evidence",
            "limit": 10,
            "token_budget": 500,
            "idempotency_key": "retrieval-before-delete",
        },
    )
    original_result = retrieval.json()["results"][0]
    assert original_result["memory_id"] == memory["memory_id"]

    deleted = await client.delete(f"/api/v1/memories/{memory['memory_id']}")
    detail = await client.get(
        f"/api/v1/memory-retrievals/{retrieval.json()['memory_retrieval_id']}"
    )

    assert deleted.status_code == 200
    assert detail.status_code == 200
    erased_result = detail.json()["results"][0]
    assert erased_result["content_text"] is None
    assert erased_result["total_score"] == original_result["total_score"]
    assert erased_result["memory_version_id"] == original_result["memory_version_id"]
