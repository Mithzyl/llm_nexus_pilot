"""Shared isolated database and HTTP fixtures for API tests."""

import os
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

os.environ["NEXUSPILOT_API_KEY"] = "test-api-key-long-enough"
os.environ["NEXUSPILOT_DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"

from nexuspilot_api.database import get_session  # noqa: E402
from nexuspilot_api.main import app  # noqa: E402
from nexuspilot_api.models import Base  # noqa: E402
from nexuspilot_api.storage import StoredObject, get_object_storage  # noqa: E402


class FakeObjectStorage:
    """Provide deterministic in-memory object metadata without contacting MinIO."""

    async def put_bytes(self, object_name: str, content: bytes, content_type: str) -> StoredObject:
        """Return stable metadata for uploaded test bytes and preserve no external state."""

        del content_type
        import hashlib

        return StoredObject(
            uri=f"memory://test/{object_name}",
            content_hash=hashlib.sha256(content).hexdigest(),
            size_bytes=len(content),
        )


@pytest_asyncio.fixture
async def client(tmp_path: Path) -> AsyncIterator[httpx.AsyncClient]:
    """Yield an authenticated ASGI client backed by a fresh SQLite database."""

    test_engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    test_factory = async_sessionmaker(test_engine, expire_on_commit=False)

    async def override_session() -> AsyncIterator[AsyncSession]:
        """Yield an isolated session connected to this test's temporary database."""

        async with test_factory() as session:
            yield session

    async with test_engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_object_storage] = FakeObjectStorage
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"X-API-Key": "test-api-key-long-enough"},
    ) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    await test_engine.dispose()
