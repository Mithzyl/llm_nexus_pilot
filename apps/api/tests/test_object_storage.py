"""Unit tests for credential-safe object-storage failure conversion."""

import pytest

from nexuspilot_api.infrastructure import object_storage
from nexuspilot_api.infrastructure.object_storage import (
    ObjectStorage,
    ObjectStorageError,
    ObjectStorageInvalidURIError,
)


async def test_put_bytes_hides_underlying_storage_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify MinIO exception details cannot leak through the storage abstraction."""

    async def fail_in_thread(*_args, **_kwargs) -> None:
        """Simulate an SDK error containing data that must not reach callers."""

        raise RuntimeError("endpoint password=do-not-expose")

    monkeypatch.setattr(object_storage.to_thread, "run_sync", fail_in_thread)
    storage = ObjectStorage()

    with pytest.raises(ObjectStorageError) as caught:
        await storage.put_bytes("run/artifact.txt", b"content", "text/plain")

    assert str(caught.value) == "Object storage operation failed."
    assert "do-not-expose" not in str(caught.value)


def test_object_uri_must_belong_to_configured_bucket() -> None:
    """Verify controlled reads reject arbitrary schemes, buckets, and traversal segments."""

    storage = ObjectStorage()

    for storage_uri in (
        "https://localhost/private",
        "minio://another-bucket/file.txt",
        f"minio://{storage.settings.minio_bucket}/../secret.txt",
    ):
        with pytest.raises(ObjectStorageInvalidURIError):
            storage._object_name_from_uri(storage_uri)


def test_stream_iterator_always_releases_http_connection() -> None:
    """Verify completed streaming closes and releases the MinIO HTTP response."""

    class FakeResponse:
        """Track response cleanup while yielding deterministic byte chunks."""

        def __init__(self) -> None:
            """Initialize cleanup flags for the simulated response."""

            self.closed = False
            self.released = False

        def stream(self, *, amt: int):
            """Yield two chunks and verify the production chunk size is bounded."""

            assert amt == 64 * 1024
            yield b"first"
            yield b"second"

        def close(self) -> None:
            """Record that the response body was closed."""

            self.closed = True

        def release_conn(self) -> None:
            """Record that the pooled connection was released."""

            self.released = True

    response = FakeResponse()
    assert b"".join(ObjectStorage._iter_response_chunks(response)) == b"firstsecond"
    assert response.closed is True
    assert response.released is True
