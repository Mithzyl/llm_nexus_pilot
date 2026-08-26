"""Unit tests for request-scoped SQLAlchemy database session lifecycle."""

import asyncio
from typing import Any

import pytest

from nexuspilot_api.infrastructure import database


class TrackingSessionContext:
    """Provide an async database session context that records rollback and close behavior."""

    def __init__(self) -> None:
        """Initialize lifecycle flags for one simulated request database session."""

        self.entered = False
        self.closed = False
        self.rolled_back = False

    async def __aenter__(self) -> "TrackingSessionContext":
        """Mark the simulated database session as opened and return it."""

        self.entered = True
        return self

    async def __aexit__(self, *_args: Any) -> None:
        """Mark the simulated database session as closed when dependency cleanup runs."""

        self.closed = True

    async def rollback(self) -> None:
        """Record that request failure caused an explicit transaction rollback."""

        self.rolled_back = True


class TrackingSessionFactory:
    """Return the same tracking context when the dependency requests a database session."""

    def __init__(self, context: TrackingSessionContext) -> None:
        """Store the context used to observe the dependency lifecycle."""

        self.context = context

    def __call__(self) -> TrackingSessionContext:
        """Return a new-request context compatible with async_sessionmaker calls."""

        return self.context


async def test_get_database_session_yields_then_closes_on_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify FastAPI receives an open database session and closes it after the response."""

    context = TrackingSessionContext()
    monkeypatch.setattr(
        database,
        "database_session_factory",
        TrackingSessionFactory(context),
    )
    dependency = database.get_database_session()

    yielded = await anext(dependency)
    assert yielded is context
    assert context.entered is True
    assert context.closed is False

    await dependency.aclose()
    assert context.closed is True
    assert context.rolled_back is False


async def test_get_database_session_rolls_back_then_closes_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify an endpoint exception is rolled back before database session cleanup."""

    context = TrackingSessionContext()
    monkeypatch.setattr(
        database,
        "database_session_factory",
        TrackingSessionFactory(context),
    )
    dependency = database.get_database_session()
    await anext(dependency)

    with pytest.raises(RuntimeError, match="request failed"):
        await dependency.athrow(RuntimeError("request failed"))

    assert context.rolled_back is True
    assert context.closed is True


async def test_get_database_session_rolls_back_cancelled_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify client cancellation rolls back before releasing the database session."""

    context = TrackingSessionContext()
    monkeypatch.setattr(
        database,
        "database_session_factory",
        TrackingSessionFactory(context),
    )
    dependency = database.get_database_session()
    await anext(dependency)

    with pytest.raises(asyncio.CancelledError):
        await dependency.athrow(asyncio.CancelledError())

    assert context.rolled_back is True
    assert context.closed is True
