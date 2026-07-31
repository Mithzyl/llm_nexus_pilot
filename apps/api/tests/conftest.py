"""Shared isolated database and HTTP fixtures for API tests."""

import os
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest_asyncio
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

os.environ["NEXUSPILOT_API_KEY"] = "test-api-key-long-enough"
os.environ["NEXUSPILOT_DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"

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
from nexuspilot_api.infrastructure.database import get_database_session  # noqa: E402
from nexuspilot_api.infrastructure.object_storage import (  # noqa: E402
    StoredObject,
    get_object_storage,
)
from nexuspilot_api.main import app  # noqa: E402
from nexuspilot_api.models import Base  # noqa: E402


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

    test_engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    test_factory = async_sessionmaker(test_engine, expire_on_commit=False)

    async with test_engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield test_factory
    await test_engine.dispose()


@pytest_asyncio.fixture
async def client(
    test_database_session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[httpx.AsyncClient]:
    """Yield an authenticated ASGI client with isolated dependencies."""

    async def override_database_session() -> AsyncIterator[AsyncSession]:
        """Yield an isolated database session connected to this test database."""

        async with test_database_session_factory() as db_session:
            yield db_session

    app.dependency_overrides[get_database_session] = override_database_session
    app.dependency_overrides[get_object_storage] = FakeObjectStorage
    app.dependency_overrides[get_provider_registry] = fake_provider_registry
    app.dependency_overrides[get_price_catalog] = fake_price_catalog
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"X-API-Key": "test-api-key-long-enough"},
    ) as test_client:
        yield test_client
    app.dependency_overrides.clear()
