"""Verify project snapshots, ownership and local copies without model requests."""

import pytest
from test_user_files import reserve_file

from nexuspilot_api.core.config import get_settings


async def create_project(client, name="My project"):
    """Create a project through the public owner-derived API."""
    response = await client.post("/api/v1/projects", json={"project_name": name})
    assert response.status_code == 201, response.text
    return response.json()["project_id"]


async def test_project_upload_copy_survives_source_deletion(resource_client, monkeypatch, tmp_path):
    """Save immutable bytes once, reject name conflicts and materialize only by explicit request."""
    client = resource_client
    project = await create_project(client)
    reservation = await reserve_file(client)
    source = reservation.json()["file_id"]
    assert (
        await client.put(f"/api/v1/files/{source}/content", content=b"hello file")
    ).status_code == 200
    payload = {"file_id": source, "filename": "notes.txt", "idempotency_key": "project-save-001"}
    url = f"/api/v1/projects/{project}/files"
    saved = await client.post(url, json=payload)
    assert saved.status_code == 201, saved.text
    entry = saved.json()
    assert "storage_uri" not in entry
    assert (await client.post(url, json=payload)).json()["project_file_id"] == entry[
        "project_file_id"
    ]
    assert (
        await client.post(url, json={**payload, "idempotency_key": "different-key"})
    ).status_code == 409
    assert (await client.post(url, json={**payload, "filename": "changed.txt"})).status_code == 409
    assert (await client.delete(f"/api/v1/files/{source}")).status_code == 204
    download = f"{url}/{entry['project_file_id']}/content"
    assert (await client.get(download)).content == b"hello file"
    monkeypatch.setattr(get_settings(), "project_workspace_root", str(tmp_path))
    materialize = f"{url}/{entry['project_file_id']}/materialize"
    assert (await client.post(materialize)).status_code == 200
    assert (await client.post(materialize)).status_code == 200
    assert list(tmp_path.rglob(entry["project_file_id"]))[0].read_bytes() == b"hello file"
    assert len((await client.get(url)).json()) == 1


@pytest.mark.parametrize("filename", ["../bad.txt", "a/b.txt", "a\\b.txt", ".", "  "])
async def test_project_filename_is_not_a_host_path(resource_client, filename):
    """Reject unsafe names before storage or filesystem writes."""
    project = await create_project(resource_client)
    source = (await reserve_file(resource_client)).json()["file_id"]
    response = await resource_client.post(
        f"/api/v1/projects/{project}/files",
        json={
            "file_id": source,
            "filename": filename,
            "idempotency_key": "reject-path-001",
        },
    )
    assert response.status_code == 422


async def test_project_sessions_keep_project_owner(resource_client):
    """Project conversations derive owner and retain their project association."""
    project = await create_project(resource_client)
    result = await resource_client.post(
        f"/api/v1/projects/{project}/sessions",
        json={
            "title": "Project conversation",
            "idempotency_key": "project-session-001",
        },
    )
    assert result.status_code == 201, result.text
    assert result.json()["project_id"] == project
    repeated = await resource_client.post(
        f"/api/v1/projects/{project}/sessions",
        json={
            "title": "Project conversation",
            "idempotency_key": "project-session-001",
        },
    )
    assert repeated.json()["session_id"] == result.json()["session_id"]
    assert (await resource_client.get(f"/api/v1/projects/{project}/sessions")).json()[0][
        "session_id"
    ] == result.json()["session_id"]
    reservation = await reserve_file(resource_client)
    file_id = reservation.json()["file_id"]
    assert (
        await resource_client.put(f"/api/v1/files/{file_id}/content", content=b"hello file")
    ).status_code == 200
    owner = result.json()["user_id"]
    turn = await resource_client.post(
        f"/api/v1/sessions/{result.json()['session_id']}/turns",
        json={
            "user_id": owner,
            "content_text": "Read this explicit attachment",
            "file_ids": [file_id],
            "idempotency_key": "project-turn-001",
        },
    )
    assert turn.status_code == 201, turn.text


async def test_project_artifact_copy_and_cross_user_access(
    resource_client, test_database_session_factory, monkeypatch
):
    """Real login users cannot read, rename, export or import another user's resources."""
    from test_authentication import login, provision_user

    monkeypatch.setenv("NEXUSPILOT_AUTHENTICATION_ENABLED", "true")
    get_settings.cache_clear()
    try:
        client = resource_client
        for owner in ("project-a", "project-b"):
            await provision_user(
                test_database_session_factory, user_id=owner, email=f"{owner}@example.test"
            )
        assert (await login(client, email="project-a@example.test")).status_code == 200
        client.headers.update(
            {"Origin": "http://test", "X-CSRF-Token": client.cookies["nexuspilot_csrf"]}
        )
        project = await create_project(client)
        run = await client.post(
            "/api/v1/runs", json={"user_id": "project-a", "user_request": "fixture only"}
        )
        artifact = await client.post(
            f"/api/v1/runs/{run.json()['run_id']}/artifacts",
            data={"artifact_type": "test"},
            files={"file": ("result.csv", b"x,y\n1,2", "text/csv")},
        )
        assert artifact.status_code == 201, artifact.text
        saved = await client.post(
            f"/api/v1/projects/{project}/files",
            json={
                "artifact_id": artifact.json()["artifact_id"],
                "filename": "result.csv",
                "idempotency_key": "artifact-save-001",
            },
        )
        assert saved.status_code == 201, saved.text
        entry = saved.json()["project_file_id"]
        assert (
            await client.get(f"/api/v1/projects/{project}/files/{entry}/content")
        ).content == b"x,y\n1,2"
        assert (
            await client.post(
                f"/api/v1/projects/{project}/workspaces",
                json={"workspace_type": "local", "credential_free_locator": "anything"},
            )
        ).status_code in {401, 403}
        assert (await login(client, email="project-b@example.test")).status_code == 200
        client.headers.update({"X-CSRF-Token": client.cookies["nexuspilot_csrf"]})
        other = await create_project(client)
        assert all(
            p["project_id"] != project
            for p in (await client.get("/api/v1/projects")).json()["items"]
        )
        for method, suffix in [
            ("GET", "files"),
            ("GET", f"files/{entry}/content"),
            ("POST", f"files/{entry}/materialize"),
            ("GET", "sessions"),
        ]:
            assert (
                await client.request(method, f"/api/v1/projects/{project}/{suffix}")
            ).status_code == 404
        assert (
            await client.patch(f"/api/v1/projects/{project}", json={"project_name": "stolen"})
        ).status_code == 404
        assert (
            await client.post(
                f"/api/v1/projects/{other}/files",
                json={
                    "artifact_id": artifact.json()["artifact_id"],
                    "filename": "stolen.csv",
                    "idempotency_key": "stolen-copy-001",
                },
            )
        ).status_code == 404
    finally:
        get_settings.cache_clear()


async def test_project_reserved_copy_can_retry_storage_failure(resource_client, monkeypatch):
    """An interrupted object write remains visible and same-key retry publishes exactly one copy."""
    from nexuspilot_api.infrastructure.object_storage import ObjectStorageError, get_object_storage
    from nexuspilot_api.main import app

    project = await create_project(resource_client)
    source = (await reserve_file(resource_client)).json()["file_id"]
    await resource_client.put(f"/api/v1/files/{source}/content", content=b"hello file")
    storage = app.dependency_overrides[get_object_storage]()
    original = storage.put_bytes

    async def fail(*args, **kwargs):
        """Interrupt only publication after the durable reservation."""
        raise ObjectStorageError("test unavailable")

    payload = {"file_id": source, "filename": "retry.txt", "idempotency_key": "retry-save-001"}
    url = f"/api/v1/projects/{project}/files"
    with monkeypatch.context() as patch:
        patch.setattr(storage, "put_bytes", fail)
        assert (await resource_client.post(url, json=payload)).status_code == 503
    pending = (await resource_client.get(url)).json()
    assert len(pending) == 1 and pending[0]["status"] == "reserved"
    assert (await resource_client.post(url, json=payload)).json()["project_file_id"] == pending[0][
        "project_file_id"
    ]
    assert storage.put_bytes == original


def test_local_project_copy_refuses_symlink_and_changed_bytes(tmp_path):
    """Exports do not follow replaced directories or overwrite an existing local version."""
    from nexuspilot_api.services.project_workspace_service import materialize_project_copy

    root = tmp_path / "root"
    root.mkdir()
    external = tmp_path / "outside"
    external.mkdir()
    (root / "project").symlink_to(external, target_is_directory=True)
    with pytest.raises(OSError):
        materialize_project_copy(str(root), "project", "file", b"bytes")
    assert list(external.iterdir()) == []
    materialize_project_copy(str(root), "safe", "file", b"original")
    with pytest.raises(ValueError):
        materialize_project_copy(str(root), "safe", "file", b"changed")
    assert (root / "safe" / "file").read_bytes() == b"original"
