"""Verify persistent file text across conversation turns without cross-session leakage."""

from datetime import UTC, datetime

import pytest
from test_context_builds import create_model_capability
from test_user_files import reserve_file


async def prepare_conversation_file(client):
    """Upload a real parsed text fixture and bind it atomically to an owner conversation."""
    await create_model_capability(client, context_window=65536)
    owner = (await client.get("/api/v1/auth/me")).json()["user_id"]
    session = (await client.post("/api/v1/sessions", json={"user_id": owner})).json()
    file = (await reserve_file(client, b"UNIQUE_FILE_TEXT_2718")).json()
    uploaded = await client.put(
        f"/api/v1/files/{file['file_id']}/content", content=b"UNIQUE_FILE_TEXT_2718"
    )
    assert uploaded.status_code == 200, uploaded.text
    first = await client.post(
        f"/api/v1/sessions/{session['session_id']}/turns",
        json={"user_id": owner, "content_text": "Read attachment", "file_ids": [file["file_id"]]},
    )
    assert first.status_code == 201, first.text
    return owner, session, file, first.json()


def context_payload(owner, session, turn, key):
    """Anchor an immutable runtime context to a concrete saved user message."""
    return {
        "user_id": owner,
        "session_id": session["session_id"],
        "run_id": turn["run"]["run_id"],
        "current_user_message_id": turn["message"]["message_id"],
        "provider": "openai",
        "model": "test-model",
        "idempotency_key": key,
    }


def assert_same_context(actual, expected):
    """Compare every snapshot field; allow only existing DATETIME second-precision rounding."""
    assert {k: v for k, v in actual.items() if k != "created_at"} == {
        k: v for k, v in expected.items() if k != "created_at"
    }
    actual_time = datetime.fromisoformat(actual["created_at"].replace("Z", "+00:00"))
    expected_time = datetime.fromisoformat(expected["created_at"].replace("Z", "+00:00"))
    assert (
        abs((actual_time.replace(tzinfo=UTC) - expected_time.replace(tzinfo=UTC)).total_seconds())
        < 1
    )


async def test_followup_provider_receives_manifest_not_file_text(resource_client):
    """Offer tools and pinned metadata on followup, without injecting unread file text."""
    from nexuspilot_models.contracts import ProviderName
    from nexuspilot_models.registry import ProviderRegistry
    from test_responses import CapturingContextProvider

    from nexuspilot_api.core.dependencies import get_provider_registry
    from nexuspilot_api.main import app

    owner, session, _, _ = await prepare_conversation_file(resource_client)
    followup = (
        await resource_client.post(
            f"/api/v1/sessions/{session['session_id']}/turns",
            json={"user_id": owner, "content_text": "Quote the file"},
        )
    ).json()
    provider = CapturingContextProvider()
    registry = ProviderRegistry()
    registry.register(ProviderName.OPENAI, provider, allowed_models=frozenset({"test-model"}))
    app.dependency_overrides[get_provider_registry] = lambda: registry
    result = await resource_client.post(
        "/api/v1/responses",
        json={
            "run_id": followup["run"]["run_id"],
            "current_user_message_id": followup["message"]["message_id"],
            "provider": "openai",
            "model": "test-model",
            "input": "Quote the file",
        },
    )
    assert result.status_code == 200, result.text
    assert all("UNIQUE_FILE_TEXT_2718" not in msg.content for msg in provider.requests[0].messages)
    assert any("file_manifest.v1" in msg.content for msg in provider.requests[0].messages)
    assert provider.requests[0].tools


async def test_followup_reuses_file_text_and_saved_context(resource_client, monkeypatch):
    """Persist manifest and hashes, but no unread text, across subsequent turns and replay."""
    client = resource_client
    owner, session, file, _ = await prepare_conversation_file(client)
    second = (
        await client.post(
            f"/api/v1/sessions/{session['session_id']}/turns",
            json={"user_id": owner, "content_text": "What was in that file?"},
        )
    ).json()

    def reject_reparse(*_):
        """Fixed upload extraction must not be parsed anew on each turn."""
        raise AssertionError("Unexpected reparse")

    monkeypatch.setattr(
        "nexuspilot_api.services.parsed_artifact_service.parse_file_content", reject_reparse
    )
    payload = context_payload(owner, session, second, "followup-context-key")
    response = await client.post("/api/v1/context-builds", json=payload)
    assert response.status_code == 201, response.text
    assert "UNIQUE_FILE_TEXT_2718" not in response.text
    assert "file_manifest.v1" in response.text
    context = response.json()
    source = next(s for s in context["sources"] if s["source_id"] == file["file_id"])
    assert source["source_type"] == "parsed_artifact"
    assert source["content_hash"] and source["source_version"]
    reloaded = await client.get(f"/api/v1/context-builds/{context['context_build_id']}")
    assert_same_context(reloaded.json(), context)
    replay = await client.post("/api/v1/context-builds", json=payload)
    assert replay.status_code == 200
    assert_same_context(replay.json(), context)


async def test_later_upload_never_changes_older_context(resource_client):
    """Context anchored before an upload cannot silently acquire that later file."""
    client = resource_client
    owner = (await client.get("/api/v1/auth/me")).json()["user_id"]
    await create_model_capability(client, context_window=65536)
    session = (await client.post("/api/v1/sessions", json={"user_id": owner})).json()
    early = (
        await client.post(
            f"/api/v1/sessions/{session['session_id']}/turns",
            json={"user_id": owner, "content_text": "Earlier question"},
        )
    ).json()
    payload = context_payload(owner, session, early, "early-context-key")
    before = await client.post("/api/v1/context-builds", json=payload)
    assert before.status_code == 201
    file = (await reserve_file(client, b"LATER_FILE_TEXT")).json()
    assert (
        await client.put(f"/api/v1/files/{file['file_id']}/content", content=b"LATER_FILE_TEXT")
    ).status_code == 200
    assert (
        await client.post(
            f"/api/v1/sessions/{session['session_id']}/turns",
            json={"user_id": owner, "content_text": "Later", "file_ids": [file["file_id"]]},
        )
    ).status_code == 201
    replay = await client.post("/api/v1/context-builds", json=payload)
    assert replay.status_code == 200
    assert_same_context(replay.json(), before.json())
    explicit = await client.post(
        "/api/v1/context-builds",
        json={
            **payload,
            "idempotency_key": "early-with-future-file",
            "source_references": {"parsed_artifacts": [{"artifact_id": file["file_id"]}]},
        },
    )
    assert explicit.status_code == 409


async def test_manifest_dispatch_rejects_replaced_parsed_object(
    resource_client, test_database_session_factory
):
    """A replayed manifest must not authorize a newly replaced parse of the same source."""
    import hashlib

    from nexuspilot_api.core.errors import ResourceConflictError
    from nexuspilot_api.infrastructure.object_storage import get_object_storage
    from nexuspilot_api.main import app
    from nexuspilot_api.models import UserFile
    from nexuspilot_api.schemas.context_builds import ContextBuildRead
    from nexuspilot_api.services.parsed_artifact_service import validate_parsed_context_sources

    owner, session, file, turn = await prepare_conversation_file(resource_client)
    response = await resource_client.post(
        "/api/v1/context-builds", json=context_payload(owner, session, turn, "pinned-manifest")
    )
    assert response.status_code == 201
    context = ContextBuildRead.model_validate(response.json())
    storage = app.dependency_overrides[get_object_storage]()
    async with test_database_session_factory() as db:
        await validate_parsed_context_sources(db, storage, context)
        row = await db.get(UserFile, file["file_id"])
        original, media_type = storage.objects[row.parsed_uri]
        replaced = original.replace(b"UNIQUE_FILE_TEXT_2718", b"UNIQUE_FILE_TEXT_0000")
        storage.objects[row.parsed_uri] = (replaced, media_type)
        row.parsed_hash = hashlib.sha256(replaced).hexdigest()
        await db.commit()
        with pytest.raises(ResourceConflictError, match="version changed"):
            await validate_parsed_context_sources(db, storage, context)


@pytest.mark.parametrize("deleted", [False, True])
async def test_other_session_excluded_and_deleted_file_not_read(resource_client, deleted):
    """Exclude other sessions and reject revoked files instead of pretending success."""
    client = resource_client
    owner, session, file, _ = await prepare_conversation_file(client)
    other = (await client.post("/api/v1/sessions", json={"user_id": owner})).json()
    turn = (
        await client.post(
            f"/api/v1/sessions/{other['session_id']}/turns",
            json={"user_id": owner, "content_text": "Unrelated"},
        )
    ).json()
    payload = context_payload(owner, other, turn, "unrelated-context-key")
    response = await client.post("/api/v1/context-builds", json=payload)
    assert response.status_code == 201
    assert "UNIQUE_FILE_TEXT_2718" not in response.text
    explicit = await client.post(
        "/api/v1/context-builds",
        json={
            **payload,
            "idempotency_key": "unrelated-explicit-key",
            "source_references": {"parsed_artifacts": [{"artifact_id": file["file_id"]}]},
        },
    )
    assert explicit.status_code == 409
    if deleted:
        assert (await client.delete(f"/api/v1/files/{file['file_id']}")).status_code == 204
        followup = (
            await client.post(
                f"/api/v1/sessions/{session['session_id']}/turns",
                json={"user_id": owner, "content_text": "Read deleted file"},
            )
        ).json()
        denied = await client.post(
            "/api/v1/context-builds",
            json=context_payload(owner, session, followup, "deleted-context-key"),
        )
        assert denied.status_code == 409
