"""Exercise private file lifecycle with isolated SQL and deterministic object storage."""

import hashlib

import pytest
from test_sessions import create_user

from nexuspilot_api.features.identity.dependencies import get_workspace_principal
from nexuspilot_api.features.identity.schemas.authentication import LocalWorkspacePrincipalRead
from nexuspilot_api.infrastructure.object_storage import ObjectStorageError, get_object_storage
from nexuspilot_api.main import app


async def reserve_file(client, content=b"hello file", filename="notes.txt"):
    """Reserve one immutable private upload using the backend-selected local owner."""

    return await client.post(
        "/api/v1/files",
        json={
            "filename": filename,
            "size_bytes": len(content),
            "content_hash": hashlib.sha256(content).hexdigest(),
            "idempotency_key": f"upload-{hashlib.sha256(content + filename.encode()).hexdigest()}",
        },
    )


async def test_upload_parse_download_delete_and_replay(client):
    """A ready upload has private metadata and verified bytes; deletion revokes reads."""

    first = await reserve_file(client)
    assert first.status_code == 201, first.text
    file_id = first.json()["file_id"]
    assert (await reserve_file(client)).json()["file_id"] == file_id
    uploaded = await client.put(f"/api/v1/files/{file_id}/content", content=b"hello file")
    assert uploaded.status_code == 200, uploaded.text
    assert uploaded.json()["status"] == "ready"
    assert "storage_uri" not in uploaded.json()
    downloaded = await client.get(f"/api/v1/files/{file_id}/content")
    assert downloaded.content == b"hello file"
    assert downloaded.headers["x-content-type-options"] == "nosniff"
    assert (await client.delete(f"/api/v1/files/{file_id}")).status_code == 204
    assert (await client.get(f"/api/v1/files/{file_id}/content")).status_code == 404


async def test_file_size_hash_and_type_fail_without_ready_state(client):
    """Unsupported files and mismatched bytes cannot be published as parsed inputs."""

    assert (await reserve_file(client, filename="image.png")).status_code == 422
    first = await reserve_file(client)
    file_id = first.json()["file_id"]
    assert (
        await client.put(f"/api/v1/files/{file_id}/content", content=b"wrong")
    ).status_code == 422
    metadata = await client.get(f"/api/v1/files/{file_id}")
    assert metadata.json()["status"] == "failed"


async def test_cleanup_does_not_delete_a_file_that_became_ready(
    client, test_database_session_factory
):
    """Cleanup must recheck readiness atomically even when its candidate snapshot is stale."""
    from nexuspilot_api.models import UserFile
    from nexuspilot_api.services.user_file_service import delete_file

    reservation = await reserve_file(client)
    file_id = reservation.json()["file_id"]
    uploaded = await client.put(f"/api/v1/files/{file_id}/content", content=b"hello file")
    assert uploaded.status_code == 200
    storage = app.dependency_overrides[get_object_storage]()
    async with test_database_session_factory() as db:
        file = await db.get(UserFile, file_id)
        assert not await delete_file(db, storage, file, cleanup_only=True)
    assert (await client.get(f"/api/v1/files/{file_id}/content")).content == b"hello file"


@pytest.mark.parametrize("after_commit", [False, True])
async def test_database_failure_after_object_write_preserves_recoverable_state(
    client, monkeypatch, after_commit
):
    """Clean failed writes without deleting ready data after an uncertain successful commit."""
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import AsyncSession

    from nexuspilot_api.models import UserFile

    file_id = (await reserve_file(client)).json()["file_id"]
    original_commit = AsyncSession.commit
    failed_once = False

    async def fail_ready_commit(db):
        """Inject one database failure exactly at the ready-state publication boundary."""
        nonlocal failed_once
        status = await db.scalar(select(UserFile.status).where(UserFile.file_id == file_id))
        if status == "ready" and not failed_once:
            failed_once = True
            if after_commit:
                await original_commit(db)
            raise RuntimeError("simulated commit outcome")
        await original_commit(db)

    monkeypatch.setattr(AsyncSession, "commit", fail_ready_commit)
    response = await client.put(f"/api/v1/files/{file_id}/content", content=b"hello file")
    assert response.status_code == 500
    assert failed_once
    metadata = (await client.get(f"/api/v1/files/{file_id}")).json()
    assert metadata["status"] == ("ready" if after_commit else "failed")
    storage = app.dependency_overrides[get_object_storage]()
    assert bool(storage.objects) == after_commit


async def test_other_user_cannot_read_write_or_delete_file(client):
    """FastAPI enforces file ownership independently of the browser proxy."""

    first = await reserve_file(client)
    file_id = first.json()["file_id"]
    await create_user(client, "other-file-owner")

    async def other_principal():
        """Supply a different already-authenticated identity without changing the file owner."""

        return LocalWorkspacePrincipalRead(
            user_id="other-file-owner",
            display_name="Other",
            authentication_method="local_deployment",
            scopes=[],
        )

    app.dependency_overrides[get_workspace_principal] = other_principal
    try:
        assert (await client.get(f"/api/v1/files/{file_id}")).status_code == 404
        assert (
            await client.put(f"/api/v1/files/{file_id}/content", content=b"hello file")
        ).status_code == 404
        assert (await client.delete(f"/api/v1/files/{file_id}")).status_code == 404
    finally:
        app.dependency_overrides.pop(get_workspace_principal, None)


async def test_quota_counts_reserved_and_failed_files(client, monkeypatch):
    """Unfinished reservations consume quota until durable cleanup succeeds."""

    from nexuspilot_api.core.config import get_settings

    monkeypatch.setattr(get_settings(), "user_file_quota_bytes", 60000)
    assert (await reserve_file(client, b"12345678")).status_code == 201
    assert (await reserve_file(client, b"1234", "other.txt")).status_code == 413


async def test_storage_failure_keeps_exact_cleanup_record(
    client, monkeypatch, test_database_session_factory
):
    """A stored source followed by parsed-object failure is cleaned by persisted retry state."""

    storage = app.dependency_overrides[get_object_storage]()
    original_put, original_delete = storage.put_bytes, storage.delete_object

    async def fail_parsed(key, content, media_type):
        """Emulate MinIO accepting the source but failing the second object."""
        if key.endswith("/parsed"):
            raise ObjectStorageError("test outage")
        return await original_put(key, content, media_type)

    async def fail_delete(uri):
        """Keep cleanup pending while storage is unavailable."""
        raise ObjectStorageError("test outage")

    first = await reserve_file(client)
    file_id = first.json()["file_id"]
    monkeypatch.setattr(storage, "put_bytes", fail_parsed)
    monkeypatch.setattr(storage, "delete_object", fail_delete)
    assert (
        await client.put(f"/api/v1/files/{file_id}/content", content=b"hello file")
    ).status_code == 503
    assert storage.objects
    monkeypatch.setattr(storage, "delete_object", original_delete)
    from nexuspilot_api.services.user_file_cleanup import cleanup_files

    async with test_database_session_factory() as db:
        assert await cleanup_files(db, storage) == {"cleaned": 1, "failed": 0}
    assert not storage.objects


@pytest.mark.parametrize("filename", ["../notes.txt", "x\\notes.txt", "bad\n.txt", "evil.exe"])
async def test_upload_rejects_paths_and_unsupported_formats(client, filename):
    """Names are display-only; no path, control character or unsupported executable is accepted."""
    assert (await reserve_file(client, filename=filename)).status_code == 422


async def test_file_mutation_requires_csrf_with_actual_login(
    client, monkeypatch, test_database_session_factory
):
    """Direct FastAPI upload mutations need CSRF, not merely a valid browser cookie."""
    from nexuspilot_api.core.config import get_settings
    from nexuspilot_api.features.identity.services.authentication_service import (
        provision_local_password_user,
    )

    monkeypatch.setenv("NEXUSPILOT_AUTHENTICATION_ENABLED", "true")
    get_settings.cache_clear()
    try:
        async with test_database_session_factory() as db:
            await provision_local_password_user(
                db,
                user_id="authenticated-file-owner",
                email="file-owner@example.test",
                password="a-valid-long-password",
                display_name="Files",
            )
        login = await client.post(
            "/api/v1/auth/login",
            headers={"Origin": "http://test"},
            json={"email": "file-owner@example.test", "password": "a-valid-long-password"},
        )
        assert login.status_code == 200, login.text
        assert (await reserve_file(client)).status_code == 403
        client.headers.update(
            {"Origin": "http://test", "X-CSRF-Token": client.cookies["nexuspilot_csrf"]}
        )
        assert (await reserve_file(client)).status_code == 201
    finally:
        get_settings.cache_clear()
