"""Shared isolated database and HTTP fixtures for API tests."""

import asyncio
import os
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

os.environ["NEXUSPILOT_API_KEY"] = "test-api-key-long-enough"
os.environ["NEXUSPILOT_INTERNAL_API_KEY"] = "test-internal-key-long-enough"
os.environ["NEXUSPILOT_DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
os.environ["NEXUSPILOT_AUTHENTICATION_ALLOWED_ORIGINS"] = "http://test"

from legacy_file_access import enforce_private_file_references
from nexuspilot_models.contracts import (  # noqa: E402
    ModelRequest,
    ModelResponse,
    ProviderName,
    StreamEvent,
    StreamEventType,
    TransportAttempt,
)
from nexuspilot_models.pricing import ModelPrice, PriceCatalog  # noqa: E402
from nexuspilot_models.registry import ProviderRegistry  # noqa: E402

from nexuspilot_api.core.dependencies import (  # noqa: E402
    get_price_catalog,
    get_provider_registry,
)
from nexuspilot_api.features.identity.resource_access import authorize_resource_request
from nexuspilot_api.infrastructure.database import (  # noqa: E402
    get_database_session,
    get_database_session_factory,
)
from nexuspilot_api.infrastructure.object_storage import (  # noqa: E402
    ObjectContent,
    ObjectStorageIntegrityError,
    ObjectStorageNotFoundError,
    StoredObject,
    get_object_storage,
)
from nexuspilot_api.main import app  # noqa: E402
from nexuspilot_api.models import Base  # noqa: E402


class FakeObjectStorage:
    """Provide deterministic in-memory upload and streaming behavior without MinIO."""

    def __init__(self) -> None:
        """Create an isolated object dictionary for one API client fixture."""

        self.objects: dict[str, tuple[bytes, str]] = {}

    async def put_bytes(self, object_name: str, content: bytes, content_type: str) -> StoredObject:
        """Persist test bytes and return stable metadata matching production uploads."""

        import hashlib

        uri = f"memory://test/{object_name}"
        self.objects[uri] = (content, content_type)
        return StoredObject(
            uri=uri,
            content_hash=hashlib.sha256(content).hexdigest(),
            size_bytes=len(content),
        )

    def uri_for_object(self, object_name: str) -> str:
        """Match deterministic test upload addresses before writing any bytes."""

        return f"memory://test/{object_name}"

    async def open_object(
        self,
        storage_uri: str,
        *,
        expected_size_bytes: int | None = None,
    ) -> ObjectContent:
        """Return stored test bytes as a lazy single-chunk object stream."""

        stored_object_entry = self.objects.get(storage_uri)
        if stored_object_entry is None:
            raise ObjectStorageNotFoundError("Stored object not found.")
        object_content, content_type = stored_object_entry
        if expected_size_bytes is not None and len(object_content) != expected_size_bytes:
            raise ObjectStorageIntegrityError("Stored object size does not match metadata.")
        return ObjectContent(
            chunks=iter([object_content]),
            size_bytes=len(object_content),
            content_type=content_type,
        )

    async def delete_object(self, storage_uri: str) -> None:
        """Remove one stored test object and ignore already-missing keys."""

        self.objects.pop(storage_uri, None)

    async def list_objects(self, prefix: str) -> list[str]:
        """Return stored object names under one deterministic prefix."""

        return [
            uri[len("memory://test/") :]
            for uri in self.objects
            if uri.startswith(f"memory://test/{prefix}")
        ]

    async def verify_object(self, storage_uri: str, expected_hash: str) -> bool:
        """Return True when stored bytes hash to the expected value."""

        import hashlib

        stored_object_entry = self.objects.get(storage_uri)
        if stored_object_entry is None:
            return False
        return hashlib.sha256(stored_object_entry[0]).hexdigest() == expected_hash


class FakeProvider:
    """Return deterministic provider-neutral responses without external network access."""

    name = ProviderName.OPENAI

    async def generate(self, request: ModelRequest) -> ModelResponse:
        """Return a completed response containing stable usage and transport evidence."""

        return ModelResponse(
            text=f"answer for {request.model}",
            finish_reason="stop",
            input_tokens=100,
            output_tokens=20,
            cached_tokens=10,
            latency_ms=5,
            provider_request_id="provider-request-1",
            raw_response={"id": "provider-request-1", "text": "answer"},
            transport_attempts=[TransportAttempt(attempt_index=1, status_code=200, latency_ms=5)],
        )

    async def stream(self, request: ModelRequest):
        """Yield a complete ordered stream ending with a normalized model response."""

        yield StreamEvent(type=StreamEventType.STARTED, sequence=1)
        yield StreamEvent(
            type=StreamEventType.TEXT_DELTA,
            sequence=2,
            data={"delta": "streamed answer"},
        )
        response = await self.generate(request)
        response = response.model_copy(update={"text": "streamed answer"})
        yield StreamEvent(
            type=StreamEventType.COMPLETED,
            sequence=3,
            data={"response": response.model_dump(mode="json")},
        )


def fake_provider_registry() -> ProviderRegistry:
    """Return a registry containing one fake OpenAI adapter for API tests."""

    registry = ProviderRegistry()
    registry.register(
        ProviderName.OPENAI,
        FakeProvider(),
        allowed_models=frozenset({"test-model"}),
    )
    return registry


def fake_price_catalog() -> PriceCatalog:
    """Return deterministic prices so API tests can verify run cost persistence."""

    from decimal import Decimal

    return PriceCatalog(
        {
            (ProviderName.OPENAI, "test-model"): ModelPrice(
                input_per_million=Decimal("10"),
                output_per_million=Decimal("30"),
                cached_input_per_million=Decimal("2"),
            )
        }
    )


@pytest_asyncio.fixture
async def test_database_session_factory(
    tmp_path: Path,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Yield a database session factory backed by a fresh database and dispose it afterward."""

    if url := os.environ.get("NEXUSPILOT_TEST_AUTH_MYSQL_URL"):
        from resource_infrastructure import authorization_mysql_factory

        async with authorization_mysql_factory(url) as factory:
            yield factory
        return

    test_engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")

    @event.listens_for(test_engine.sync_engine, "connect")
    def enable_sqlite_foreign_keys(dbapi_connection: object, _connection_record: object) -> None:
        """Enable SQLite foreign keys so fast tests detect production insert-order defects."""

        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    test_factory = async_sessionmaker(test_engine, expire_on_commit=False)

    async with test_engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield test_factory
    await test_engine.dispose()


@pytest_asyncio.fixture
async def client(
    test_database_session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[httpx.AsyncClient]:
    """Run legacy business contracts with trusted fixture setup, not end-user authorization.

    Historical tests create arbitrary owners. Keep their existing private-file
    checks. Use resource_client for actual new authorization, without this override.
    This dependency replacement exists only in tests, never application settings.
    """

    async def override_database_session() -> AsyncIterator[AsyncSession]:
        """Yield an isolated database session connected to this test database."""

        async with test_database_session_factory() as db_session:
            try:
                yield db_session
            except (Exception, asyncio.CancelledError):
                await db_session.rollback()
                raise

    fake_object_storage = FakeObjectStorage()
    app.dependency_overrides[get_database_session] = override_database_session
    app.dependency_overrides[get_database_session_factory] = lambda: test_database_session_factory
    app.dependency_overrides[get_object_storage] = lambda: fake_object_storage
    app.dependency_overrides[get_provider_registry] = fake_provider_registry
    app.dependency_overrides[get_price_catalog] = fake_price_catalog
    app.dependency_overrides[authorize_resource_request] = enforce_private_file_references
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"X-API-Key": "test-api-key-long-enough"},
    ) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def resource_client(client):
    """Exercise real authorization; optional MinIO settings must name a loopback test instance."""
    app.dependency_overrides.pop(authorize_resource_request, None)
    if endpoint := os.environ.get("NEXUSPILOT_TEST_AUTH_MINIO_ENDPOINT"):
        from nexuspilot_api.core.config import Settings
        from nexuspilot_api.infrastructure.object_storage import ObjectStorage

        if not endpoint.startswith(("127.0.0.1:", "localhost:")):
            raise ValueError("Authorization tests require a loopback MinIO")
        storage = ObjectStorage(
            Settings(
                _env_file=None,
                minio_endpoint=endpoint,
                minio_access_key=os.environ["NEXUSPILOT_TEST_AUTH_MINIO_ACCESS_KEY"],
                minio_secret_key=os.environ["NEXUSPILOT_TEST_AUTH_MINIO_SECRET_KEY"],
                minio_bucket="authorization-audit",
                minio_secure=False,
            )
        )
        app.dependency_overrides[get_object_storage] = lambda: storage
    yield client
