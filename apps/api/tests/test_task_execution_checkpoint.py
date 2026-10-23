"""Check immutable general-loop checkpoints without treating them as restart support."""

import pytest
from sqlalchemy import select
from test_agent_workflows import register_default_agent_test_model, scripted_registry  # noqa: F401
from test_api import create_test_run
from test_general_agent import GeneralProvider, general_model_catalog, general_payload  # noqa: F401

from nexuspilot_api.core.dependencies import get_provider_registry
from nexuspilot_api.core.errors import ResourceConflictError, ResourceNotFoundError
from nexuspilot_api.features.agent_runtime.services.task_execution_checkpoint import (
    SCHEMA,
    read_task_execution_checkpoint,
    save_task_execution_checkpoint,
)
from nexuspilot_api.infrastructure.object_storage import get_object_storage
from nexuspilot_api.main import app
from nexuspilot_api.models import LlmMemorySnapshotObject, User, new_id


@pytest.mark.parametrize("use_tool", [False, True])
async def test_general_loop_saves_checkpoint_and_replays_without_duplicate_object(
    client, test_database_session_factory, use_tool,
):
    """A completed model turn creates a durable observation readable only in its task scope."""
    run = await create_test_run(client)
    provider = GeneralProvider(tool=use_tool)
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)
    response = await client.post(f"/api/v1/runs/{run['run_id']}/agent-workflows",
                                 json=general_payload("never"))
    assert response.json()["status"] == "completed", response.text
    storage = app.dependency_overrides[get_object_storage]()
    async with test_database_session_factory() as db:
        rows = list(await db.scalars(select(LlmMemorySnapshotObject).where(
            LlmMemorySnapshotObject.run_id == run["run_id"],
            LlmMemorySnapshotObject.schema_version == SCHEMA,
            LlmMemorySnapshotObject.mime_type == "application/json",
        ).order_by(LlmMemorySnapshotObject.object_version)))
        assert len(rows) == (3 if use_tool else 1)
        previous_id = None
        for snapshot in rows:
            restored = await read_task_execution_checkpoint(
                db, storage, run_id=run["run_id"], task_id=snapshot.task_id,
                snapshot_id=snapshot.memory_snapshot_object_id,
            )
            assert restored["previous_snapshot_id"] == previous_id
            previous_id = snapshot.memory_snapshot_object_id
        row = rows[0]
        body = await read_task_execution_checkpoint(db, storage, run_id=run["run_id"],
            task_id=row.task_id, snapshot_id=row.memory_snapshot_object_id)
        assert body["previous_snapshot_id"] is None
        state = body["state"]
        assert state["phase"] == "model_response"
        assert state["objective"] == "Investigate a repository"
        assert state["automatic_resume_supported"] is False
        count = len(storage.objects)
        replay = await save_task_execution_checkpoint(db, storage, run_id=run["run_id"],
            task_id=row.task_id, attempt_id=state["attempt_id"], phase="model_response",
            tool_records=[], loaded_versions=state["loaded_tool_versions"])
        assert replay == row.memory_snapshot_object_id
        assert len(storage.objects) == count
        with pytest.raises(ResourceNotFoundError):
            await read_task_execution_checkpoint(db, storage, run_id=new_id(),
                task_id=row.task_id, snapshot_id=row.memory_snapshot_object_id)
        content, mime = storage.objects[row.storage_uri]
        storage.objects[row.storage_uri] = (b"corrupt", mime)
        with pytest.raises(ResourceConflictError):
            await read_task_execution_checkpoint(db, storage, run_id=run["run_id"],
                task_id=row.task_id, snapshot_id=row.memory_snapshot_object_id)
        storage.objects[row.storage_uri] = (content, mime)


async def test_checkpoint_hash_failure_does_not_register_success_or_continue_model(
    client, test_database_session_factory, monkeypatch,
):
    """Failed object verification cannot publish a checkpoint or a completed final answer."""
    run = await create_test_run(client)
    provider = GeneralProvider(tool=True)
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)
    storage = app.dependency_overrides[get_object_storage]()

    async def reject_hash(*args, **kwargs):
        """Simulate an object-store verification failure before SQL registration."""
        return False

    monkeypatch.setattr(storage, "verify_object", reject_hash)
    response = await client.post(f"/api/v1/runs/{run['run_id']}/agent-workflows",
                                 json=general_payload("never"))
    assert response.json()["status"] == "failed"
    assert response.json()["final_output"] is None
    assert len(provider.requests) == 1
    async with test_database_session_factory() as db:
        rows = list(await db.scalars(select(LlmMemorySnapshotObject).where(
            LlmMemorySnapshotObject.run_id == run["run_id"],
            LlmMemorySnapshotObject.schema_version == SCHEMA,
        )))
        assert rows == []


async def test_checkpoint_read_rejects_wrong_owner(
    client, test_database_session_factory,
):
    """Recovery rejects a snapshot registered to a different user from its Run."""
    run = await create_test_run(client)
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(GeneralProvider())
    response = await client.post(f"/api/v1/runs/{run['run_id']}/agent-workflows",
                                 json=general_payload("never"))
    assert response.json()["status"] == "completed", response.text
    storage = app.dependency_overrides[get_object_storage]()
    async with test_database_session_factory() as db:
        row = await db.scalar(select(LlmMemorySnapshotObject).where(
            LlmMemorySnapshotObject.run_id == run["run_id"],
            LlmMemorySnapshotObject.schema_version == SCHEMA,
            LlmMemorySnapshotObject.mime_type == "application/json",
        ))
        other = User(user_id=new_id(), display_name="Other checkpoint owner")
        db.add(other)
        await db.flush()
        row.user_id = other.user_id
        with pytest.raises(ResourceNotFoundError):
            await read_task_execution_checkpoint(
                db, storage, run_id=run["run_id"], task_id=row.task_id,
                snapshot_id=row.memory_snapshot_object_id,
            )
