"""Require object-backed conversation bodies without changing public read contracts."""

import pytest
from sqlalchemy import text
from test_sessions import append_message, create_session, create_user


async def test_new_message_body_is_not_saved_in_sql(client, test_database_session_factory):
    """Persist an opaque object reference and restore the exact body on a new API request."""
    await create_user(client, "body-owner")
    session = await create_session(client, "body-owner")
    message = await append_message(client, session["session_id"], "PRIVATE_BODY_123")
    async with test_database_session_factory() as db:
        row = (
            await db.execute(
                text("SELECT content_text, body_ref_json FROM llm_messages WHERE message_id=:id"),
                {"id": message["message_id"]},
            )
        ).one()
        assert row.content_text is None
        assert row.body_ref_json
    result = await client.get(f"/api/v1/messages/{message['message_id']}")
    assert result.status_code == 200
    assert result.json()["content_text"] == "PRIVATE_BODY_123"
    assert "body_ref_json" not in result.json()


async def test_legacy_inline_message_remains_readable(client, test_database_session_factory):
    """Old rows with no object reference remain readable without silently changing history."""
    await create_user(client, "legacy-body-owner")
    session = await create_session(client, "legacy-body-owner")
    message = await append_message(client, session["session_id"], "initial")
    async with test_database_session_factory() as db:
        await db.execute(
            text(
                "UPDATE llm_messages SET body_ref_json=NULL, content_text='legacy full text' "
                "WHERE message_id=:id"
            ),
            {"id": message["message_id"]},
        )
        await db.commit()
    result = await client.get(f"/api/v1/messages/{message['message_id']}")
    assert result.json()["content_text"] == "legacy full text"


@pytest.mark.parametrize("corrupt", [False, True])
async def test_missing_or_corrupt_body_fails_instead_of_returning_empty(client, corrupt):
    """Referenced objects may never fall back to a blank SQL placeholder on read failure."""
    from nexuspilot_api.infrastructure.object_storage import get_object_storage
    from nexuspilot_api.main import app

    await create_user(client, "broken-body-owner")
    session = await create_session(client, "broken-body-owner")
    message = await append_message(client, session["session_id"], "body that must survive")
    storage = app.dependency_overrides[get_object_storage]()
    key = next(k for k in storage.objects if "/llm_messages/" in k)
    if corrupt:
        storage.objects[key] = (b"tampered", "application/json")
    else:
        del storage.objects[key]
    response = await client.get(f"/api/v1/messages/{message['message_id']}")
    assert response.status_code == 409
    assert "body that must survive" not in response.text


async def test_cross_user_denial_does_not_open_body_object(resource_client, monkeypatch):
    """Check persisted ownership before object reads, including when the body is unavailable."""
    from nexuspilot_api.features.identity.schemas.authentication import LocalWorkspacePrincipalRead
    from nexuspilot_api.infrastructure.object_storage import get_object_storage
    from nexuspilot_api.main import app

    owner = (await resource_client.get("/api/v1/auth/me")).json()["user_id"]
    session = (await resource_client.post("/api/v1/sessions", json={"user_id": owner})).json()
    turn = (
        await resource_client.post(
            f"/api/v1/sessions/{session['session_id']}/turns",
            json={
                "user_id": owner,
                "content_text": "OWNER_ONLY_BODY",
            },
        )
    ).json()
    storage = app.dependency_overrides[get_object_storage]()

    async def other_principal(*args):
        """Supply another authenticated principal for real resource authorization."""
        return LocalWorkspacePrincipalRead(user_id="not-the-owner", display_name="Other", scopes=[])

    async def forbidden_open(*args, **kwargs):
        """An unauthorized request must never consult a private object body."""
        raise AssertionError("Unauthorized body read")

    monkeypatch.setattr(
        "nexuspilot_api.features.identity.resource_access.get_workspace_principal", other_principal
    )
    monkeypatch.setattr(storage, "open_object", forbidden_open)
    for path in (
        f"messages/{turn['message']['message_id']}",
        f"sessions/{session['session_id']}/messages/latest",
    ):
        response = await resource_client.get(f"/api/v1/{path}")
        assert response.status_code == 404
        assert "OWNER_ONLY_BODY" not in response.text


async def test_reference_transplant_is_rejected(client, test_database_session_factory):
    """A valid hash and URI for another message cannot be transplanted into this row."""
    await create_user(client, "transplant-owner")
    session = await create_session(client, "transplant-owner")
    first = await append_message(client, session["session_id"], "one")
    second = await append_message(client, session["session_id"], "two")
    async with test_database_session_factory() as db:
        reference = (
            await db.execute(
                text("SELECT body_ref_json FROM llm_messages WHERE message_id=:id"),
                {"id": first["message_id"]},
            )
        ).scalar()
        await db.execute(
            text("UPDATE llm_messages SET body_ref_json=:ref WHERE message_id=:id"),
            {"ref": reference, "id": second["message_id"]},
        )
        await db.commit()
    assert (await client.get(f"/api/v1/messages/{second['message_id']}")).status_code == 409


async def test_rollback_leaves_exact_reconciliation_receipt(client, test_database_session_factory):
    """SQL rollback cannot erase the durable receipt identifying each uncommitted body object."""
    import json

    from nexuspilot_api.infrastructure.object_storage import get_object_storage
    from nexuspilot_api.main import app
    from nexuspilot_api.models import LlmMessage, MessageRole

    await create_user(client, "rollback-body-owner")
    session = await create_session(client, "rollback-body-owner")
    async with test_database_session_factory() as db:
        message = LlmMessage(
            session_id=session["session_id"],
            sequence=1,
            role=MessageRole.USER,
            content_text="rolled back body",
            metadata_json={},
        )
        db.add(message)
        await db.flush()
        identifier = message.message_id
        await db.rollback()
        assert await db.get(LlmMessage, identifier) is None
    storage = app.dependency_overrides[get_object_storage]()
    receipts = [
        json.loads(value[0])
        for key, value in storage.objects.items()
        if "conversation-body-writes/" in key
    ]
    receipt = next(r for r in receipts if r["record_id"] == identifier)
    assert receipt["reference"]["uri"] in storage.objects


async def test_bounded_backfill_and_verified_restore_preserve_legacy_text(
    client, test_database_session_factory
):
    """Dry-run does not write; applied backfill and offline restore preserve exact old bodies."""
    from nexuspilot_api.services.conversation_body_maintenance import migrate_batch

    await create_user(client, "backfill-owner")
    session = await create_session(client, "backfill-owner")
    message = await append_message(client, session["session_id"], "initial")
    async with test_database_session_factory() as db:
        await db.execute(
            text(
                "UPDATE llm_messages SET body_ref_json=NULL, content_text='LEGACY_BODY' "
                "WHERE message_id=:id"
            ),
            {"id": message["message_id"]},
        )
        await db.commit()
        assert (await migrate_batch(db, "llm_messages", limit=1))["applied"] is False
        assert (
            await db.execute(text("SELECT content_text FROM llm_messages"))
        ).scalar() == "LEGACY_BODY"
        assert (await migrate_batch(db, "llm_messages", limit=1, apply=True))["count"] == 1
        assert (await db.execute(text("SELECT content_text FROM llm_messages"))).scalar() is None
    assert (await client.get(f"/api/v1/messages/{message['message_id']}")).json()[
        "content_text"
    ] == "LEGACY_BODY"
    async with test_database_session_factory() as db:
        assert (await migrate_batch(db, "llm_messages", limit=1, apply=True, restore=True))[
            "count"
        ] == 1
        row = (await db.execute(text("SELECT content_text, body_ref_json FROM llm_messages"))).one()
        assert row.content_text == "LEGACY_BODY" and row.body_ref_json is None


async def test_reconcile_never_deletes_live_or_recent_objects(
    client, test_database_session_factory
):
    """Maintenance preserves referenced bodies and recent writes, and uses exact orphan keys."""
    import json
    from datetime import UTC, datetime, timedelta

    from nexuspilot_api.infrastructure.object_storage import get_object_storage
    from nexuspilot_api.main import app
    from nexuspilot_api.services.conversation_body_maintenance import reconcile_receipt

    await create_user(client, "receipt-owner")
    session = await create_session(client, "receipt-owner")
    await append_message(client, session["session_id"], "live body")
    storage = app.dependency_overrides[get_object_storage]()
    receipt = next(
        json.loads(v[0]) for k, v in storage.objects.items() if "conversation-body-writes/" in k
    )
    async with test_database_session_factory() as db:
        assert await reconcile_receipt(db, storage, receipt, apply=True) == "too_recent"
        later = datetime.now(UTC) + timedelta(days=2)
        assert await reconcile_receipt(db, storage, receipt, apply=True, now=later) == "referenced"
        await db.execute(text("DELETE FROM llm_messages"))
        await db.commit()
        assert await reconcile_receipt(db, storage, receipt, now=later) == "unreferenced"
        assert receipt["reference"]["uri"] in storage.objects
        assert await reconcile_receipt(db, storage, receipt, apply=True, now=later) == "deleted"
        assert receipt["reference"]["uri"] not in storage.objects


async def test_failed_body_write_does_not_commit_a_blank_message(
    client, test_database_session_factory, monkeypatch
):
    """A failed upload rolls SQL back rather than saving a placeholder without a readable body."""
    from nexuspilot_api.infrastructure.object_storage import ObjectStorageError, get_object_storage
    from nexuspilot_api.main import app
    from nexuspilot_api.models import LlmMessage, MessageRole

    await create_user(client, "write-failure-owner")
    session = await create_session(client, "write-failure-owner")
    storage = app.dependency_overrides[get_object_storage]()
    original = storage.put_bytes

    async def fail_body(name, content, content_type):
        """Allow the durable receipt, then simulate an unavailable body upload."""
        if name.startswith("conversation-bodies/"):
            raise ObjectStorageError("unavailable")
        return await original(name, content, content_type)

    monkeypatch.setattr(storage, "put_bytes", fail_body)
    async with test_database_session_factory() as db:
        db.add(
            LlmMessage(
                session_id=session["session_id"],
                sequence=1,
                role=MessageRole.USER,
                content_text="not committed",
                metadata_json={},
            )
        )
        with pytest.raises(ObjectStorageError):
            await db.commit()
        assert (await db.execute(text("SELECT COUNT(*) FROM llm_messages"))).scalar() == 0


async def test_uncertain_sql_commit_keeps_committed_body_readable(
    client, test_database_session_factory, monkeypatch
):
    """A lost commit acknowledgement must never trigger deletion of an already committed body."""
    from sqlalchemy.ext.asyncio import AsyncSession

    from nexuspilot_api.models import LlmMessage, MessageRole

    await create_user(client, "uncertain-owner")
    session = await create_session(client, "uncertain-owner")
    original = AsyncSession.commit

    async def commit_then_lose_ack(db):
        """Commit successfully but report a connection failure to the caller."""
        await original(db)
        raise ConnectionError("acknowledgement lost")

    async with test_database_session_factory() as db:
        message = LlmMessage(
            session_id=session["session_id"],
            sequence=1,
            role=MessageRole.USER,
            content_text="committed body",
            metadata_json={},
        )
        db.add(message)
        with monkeypatch.context() as patch:
            patch.setattr(AsyncSession, "commit", commit_then_lose_ack)
            with pytest.raises(ConnectionError):
                await db.commit()
        identifier = message.message_id
    async with test_database_session_factory() as db:
        assert (await db.get(LlmMessage, identifier)).content_text == "committed body"


async def test_reconciliation_command_retains_receipt_for_live_then_superseded_body(
    client, test_database_session_factory, monkeypatch
):
    """Keep a live receipt so an object superseded later can still be reconciled by exact key."""
    import json
    from datetime import UTC, datetime, timedelta
    from types import SimpleNamespace

    from nexuspilot_api.infrastructure import database
    from nexuspilot_api.infrastructure.object_storage import get_object_storage
    from nexuspilot_api.main import app
    from nexuspilot_api.services.conversation_body_maintenance import run

    await create_user(client, "receipt-command-owner")
    session = await create_session(client, "receipt-command-owner")
    await append_message(client, session["session_id"], "live receipt body")
    storage = app.dependency_overrides[get_object_storage]()
    uri = next(key for key in storage.objects if "conversation-body-writes/" in key)
    receipt = json.loads(storage.objects[uri][0])
    receipt["created_at"] = (datetime.now(UTC) - timedelta(days=2)).isoformat()
    storage.objects[uri] = (json.dumps(receipt).encode(), "application/json")

    async def list_page(prefix, *, after, limit):
        """Expose one bounded fake page through the same maintenance interface as MinIO."""
        return [
            name for name in await storage.list_objects(prefix) if after is None or name > after
        ][:limit]

    monkeypatch.setattr(storage, "list_object_page", list_page, raising=False)
    monkeypatch.setattr(database, "database_session_factory", test_database_session_factory)
    args = SimpleNamespace(operation="reconcile", after=None, limit=100, apply=True)
    assert (await run(args))["results"][0]["status"] == "referenced"
    assert uri in storage.objects
    async with test_database_session_factory() as db:
        await db.execute(text("DELETE FROM llm_messages"))
        await db.commit()
    assert (await run(args))["results"][0]["status"] == "deleted"
    assert uri not in storage.objects
    assert receipt["reference"]["uri"] not in storage.objects
