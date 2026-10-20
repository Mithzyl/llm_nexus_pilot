"""Offline in-flight persistence checks using SQLite and the existing object-store fixture."""

import asyncio
import json
from pathlib import Path

import pytest
from nexuspilot_models.contracts import ProviderName, StreamEvent, StreamEventType
from nexuspilot_models.providers.common import preserve_stream_usage
from nexuspilot_models.registry import ProviderRegistry
from test_api import create_test_run

from nexuspilot_api.core.dependencies import get_provider_registry
from nexuspilot_api.infrastructure.object_storage import ObjectStorageError, get_object_storage
from nexuspilot_api.main import app
from nexuspilot_api.services import model_response_service


class IdleProvider:
    """Expose a collected streaming request that remains alive until explicitly cancelled."""

    def __init__(self, waiting):
        """Expose receipt and closure signals for deterministic cancellation checks."""
        self.waiting = waiting
        self.closed = asyncio.Event()

    @preserve_stream_usage
    async def stream(self, request):
        """Simulate a finish observation without DONE, then idle without approving completion."""
        request._stream_protocol_evidence.update({
            "protocol": "chat_completions_sse",
            "finish_reason": "stop",
            "done_marker_received": False,
        })
        try:
            yield StreamEvent(
                type=StreamEventType.TEXT_DELTA, sequence=1, data={"delta": "已收到的文字"},
            )
            self.waiting.set()
            await asyncio.Event().wait()
        finally:
            self.closed.set()

    async def generate(self, request):
        """Consume the stream exactly as a collected Agent call does."""
        async for _ in self.stream(request):
            pass
        raise AssertionError("Must not complete")


@pytest.mark.parametrize("stream", [False, True])
async def test_idle_provider_preserves_received_progress_before_cancellation(
    client, monkeypatch, stream, test_database_session_factory,
):
    """Persist and report cancelled protocol observations, preserving unknown legacy fields."""
    monkeypatch.setattr(
        model_response_service, "STREAM_PROGRESS_INTERVAL_SECONDS", 0.02, raising=False,
    )
    waiting = asyncio.Event()

    registry = ProviderRegistry()
    provider = IdleProvider(waiting)
    registry.register(
        ProviderName.OPENAI, provider, allowed_models=frozenset({"test-model"}),
    )
    app.dependency_overrides[get_provider_registry] = lambda: registry
    storage = app.dependency_overrides[get_object_storage]()
    original_put = storage.put_bytes
    progress_writes = []

    async def count_progress_writes(name, content, content_type):
        """Count only periodic batches so idle polling cannot masquerade as new output."""
        if name.endswith("/stream-progress.json"):
            progress_writes.append(name)
        return await original_put(name, content, content_type)

    monkeypatch.setattr(storage, "put_bytes", count_progress_writes)
    run = await create_test_run(client)
    task = asyncio.create_task(client.post("/api/v1/responses", json={
        "run_id": run["run_id"], "provider": "openai", "model": "test-model",
        "input": "Test progress", "timeout_seconds": None, "stream": stream,
    }))
    try:
        await asyncio.wait_for(waiting.wait(), timeout=2)
        attempt = None
        for _ in range(40):
            detail = (await client.get(f"/api/v1/runs/{run['run_id']}")).json()
            attempt = detail["attempts"][0]
            if attempt["raw_response_uri"]:
                break
            await asyncio.sleep(0.02)
        assert attempt["status"] == "started"
        assert attempt["raw_response_uri"] is not None
        raw = json.loads(storage.objects[attempt["raw_response_uri"]][0])
        assert raw["stream_progress"]["text"] == "已收到的文字"
        assert raw["stream_progress"]["response_complete"] is False
        assert raw["stream_progress"]["protocol_evidence"]["finish_reason"] == "stop"
        assert raw["stream_progress"]["protocol_evidence"]["done_marker_received"] is False
        assert not task.done()
        await asyncio.sleep(0.06)
        assert len(progress_writes) == 1
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    detail = (await client.get(f"/api/v1/runs/{run['run_id']}")).json()
    assert detail["attempts"][0]["status"] == "outcome_unknown"
    final_uri = detail["attempts"][0]["raw_response_uri"]
    final_raw = json.loads(storage.objects[final_uri][0])
    assert (
        final_raw["stream_progress"]["protocol_evidence"]
        == raw["stream_progress"]["protocol_evidence"]
    )
    assert provider.closed.is_set()
    monkeypatch.syspath_prepend(str(
        Path(__file__).resolve().parents[3] / "deployments" / "verification"
    ))
    import check_live_general_agent as verification

    monkeypatch.setattr(verification, "database_session_factory", test_database_session_factory)
    monkeypatch.setattr(verification, "ObjectStorage", lambda: storage)
    report = await verification.stream_progress_evidence(run["run_id"])
    assert report[attempt["attempt_id"]]["protocol_evidence"] == (
        final_raw["stream_progress"]["protocol_evidence"]
    )
    # A historical object without observations must not become "DONE not received".
    del final_raw["stream_progress"]["protocol_evidence"]
    _, mime = storage.objects[final_uri]
    storage.objects[final_uri] = (json.dumps(final_raw).encode(), mime)
    legacy_report = await verification.stream_progress_evidence(run["run_id"])
    assert legacy_report[attempt["attempt_id"]]["protocol_evidence"] is None


async def test_progress_storage_failure_stops_provider_without_claiming_completion(
    client, monkeypatch,
):
    """An idle provider is interrupted promptly when its periodic audit write fails."""
    monkeypatch.setattr(model_response_service, "STREAM_PROGRESS_INTERVAL_SECONDS", 0.02)
    storage = app.dependency_overrides[get_object_storage]()
    original_put = storage.put_bytes

    async def fail_progress(name, content, content_type):
        """Reject only periodic snapshots, allowing the final failure record to persist."""
        if name.endswith("/stream-progress.json"):
            raise ObjectStorageError("synthetic progress write failure")
        return await original_put(name, content, content_type)

    monkeypatch.setattr(storage, "put_bytes", fail_progress)
    provider = IdleProvider(asyncio.Event())
    registry = ProviderRegistry()
    registry.register(
        ProviderName.OPENAI, provider, allowed_models=frozenset({"test-model"}),
    )
    app.dependency_overrides[get_provider_registry] = lambda: registry
    run = await create_test_run(client)
    response = await asyncio.wait_for(client.post("/api/v1/responses", json={
        "run_id": run["run_id"], "provider": "openai", "model": "test-model",
        "input": "Test failed progress", "timeout_seconds": None,
    }), timeout=2)
    assert response.status_code == 502
    assert response.json()["error"]["type"] == "stream_progress_persistence_error"
    assert provider.closed.is_set()
    detail = (await client.get(f"/api/v1/runs/{run['run_id']}")).json()
    assert len(detail["attempts"]) == 1
    attempt = detail["attempts"][0]
    assert attempt["status"] == "failed"
    assert attempt["estimated_cost"] is None
    raw = json.loads(storage.objects[attempt["raw_response_uri"]][0])
    assert raw["stream_progress"]["text"] == "已收到的文字"


async def test_late_progress_link_cannot_replace_terminal_response(
    client, test_database_session_factory, monkeypatch,
):
    """A completion between the snapshot read and PUT wins over the late SQL update."""
    from sqlalchemy import update

    from nexuspilot_api.models import AttemptStatus, LlmModelAttempt
    from nexuspilot_api.services.model_stream_progress_store import save_inflight_progress

    run = await create_test_run(client)
    response = await client.post("/api/v1/responses", json={
        "run_id": run["run_id"], "provider": "openai", "model": "test-model", "input": "fixture",
    })
    assert response.status_code == 200
    attempt = (await client.get(f"/api/v1/runs/{run['run_id']}")).json()["attempts"][0]
    attempt_id = attempt["attempt_id"]
    final_uri = attempt["raw_response_uri"]
    async with test_database_session_factory() as db:
        await db.execute(update(LlmModelAttempt).where(
            LlmModelAttempt.attempt_id == attempt_id,
        ).values(status=AttemptStatus.STARTED))
        await db.commit()
    storage = app.dependency_overrides[get_object_storage]()
    original_put = storage.put_bytes

    async def complete_before_put_returns(name, content, content_type):
        """Commit the authoritative terminal result while the old progress batch is in flight."""
        stored = await original_put(name, content, content_type)
        async with test_database_session_factory() as db:
            await db.execute(update(LlmModelAttempt).where(
                LlmModelAttempt.attempt_id == attempt_id,
            ).values(status=AttemptStatus.COMPLETED, raw_response_uri=final_uri))
            await db.commit()
        return stored

    monkeypatch.setattr(storage, "put_bytes", complete_before_put_returns)
    await save_inflight_progress(
        test_database_session_factory, storage, attempt_id=attempt_id,
        progress={"text": "old progress", "response_complete": False},
    )
    detail = (await client.get(f"/api/v1/runs/{run['run_id']}")).json()["attempts"][0]
    assert detail["status"] == "completed"
    assert detail["raw_response_uri"] == final_uri
