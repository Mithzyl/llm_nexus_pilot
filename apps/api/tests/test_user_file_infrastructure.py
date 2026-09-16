"""Optional real-MySQL/MinIO upload acceptance; never uses the developer database implicitly.

Set NEXUSPILOT_TEST_FILE_DATABASE_URL (isolated, migrated to head),
NEXUSPILOT_TEST_FILE_MINIO_ENDPOINT, NEXUSPILOT_TEST_FILE_MINIO_ACCESS_KEY and
NEXUSPILOT_TEST_FILE_MINIO_SECRET_KEY. Only this test's owner and objects are removed.
"""

import asyncio
import hashlib
import os
import uuid

import httpx
import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from nexuspilot_api.core.config import Settings, get_settings
from nexuspilot_api.features.identity.dependencies import get_workspace_principal
from nexuspilot_api.features.identity.schemas.authentication import LocalWorkspacePrincipalRead
from nexuspilot_api.infrastructure.database import get_database_session
from nexuspilot_api.infrastructure.object_storage import ObjectStorage, get_object_storage
from nexuspilot_api.main import app
from nexuspilot_api.models import User, UserFile


@pytest.mark.skipif(
    not os.environ.get("NEXUSPILOT_TEST_FILE_DATABASE_URL"),
    reason="Requires explicit isolated MySQL and MinIO configuration",
)
async def test_mysql_reservations_are_serialized_and_minio_bytes_are_verified(monkeypatch):
    """Independent MySQL transactions cannot double-reserve a key or overspend file quota."""
    engine = create_async_engine(os.environ["NEXUSPILOT_TEST_FILE_DATABASE_URL"])
    factory = async_sessionmaker(engine, expire_on_commit=False)
    owner = f"upload-test-{uuid.uuid4().hex}"
    storage = ObjectStorage(
        Settings(
            _env_file=None,
            minio_endpoint=os.environ["NEXUSPILOT_TEST_FILE_MINIO_ENDPOINT"],
            minio_access_key=os.environ["NEXUSPILOT_TEST_FILE_MINIO_ACCESS_KEY"],
            minio_secret_key=os.environ["NEXUSPILOT_TEST_FILE_MINIO_SECRET_KEY"],
            minio_bucket="upload-acceptance-test",
        )
    )
    original = dict(app.dependency_overrides)

    async def session():
        """Use one independent connection transaction per actual HTTP request."""
        async with factory() as db:
            yield db

    async def principal():
        """Emulate an already authenticated isolated test owner, not a client-supplied owner."""
        return LocalWorkspacePrincipalRead(user_id=owner, display_name="test", scopes=[])

    try:
        async with factory() as db:
            db.add(User(user_id=owner, display_name="upload test"))
            await db.commit()
        app.dependency_overrides[get_database_session] = session
        app.dependency_overrides[get_workspace_principal] = principal
        app.dependency_overrides[get_object_storage] = lambda: storage
        monkeypatch.setattr(get_settings(), "user_file_quota_bytes", 60000)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
            headers={"X-API-Key": get_settings().api_key},
        ) as client:
            payload = {
                "filename": "notes.txt",
                "size_bytes": 5,
                "content_hash": hashlib.sha256(b"hello").hexdigest(),
                "idempotency_key": "concurrent-upload-key",
            }
            replies = await asyncio.gather(
                *(client.post("/api/v1/files", json=payload) for _ in range(6))
            )
            assert all(reply.status_code == 201 for reply in replies), [r.text for r in replies]
            ids = {reply.json()["file_id"] for reply in replies}
            assert len(ids) == 1
            assert (
                await client.post(
                    "/api/v1/files", json={**payload, "idempotency_key": "another-upload-key"}
                )
            ).status_code == 413
            file_id = ids.pop()
            assert (
                await client.put(f"/api/v1/files/{file_id}/content", content=b"hello")
            ).status_code == 200
            assert (await client.get(f"/api/v1/files/{file_id}/content")).content == b"hello"
            assert (await client.delete(f"/api/v1/files/{file_id}")).status_code == 204
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(original)
        async with factory() as db:
            from sqlalchemy import select

            files = list(
                (await db.scalars(select(UserFile).where(UserFile.user_id == owner))).all()
            )
            for file in files:
                await storage.delete_object(file.storage_uri)
                await storage.delete_object(file.parsed_uri)
            await db.execute(delete(UserFile).where(UserFile.user_id == owner))
            await db.execute(delete(User).where(User.user_id == owner))
            await db.commit()
        await engine.dispose()
