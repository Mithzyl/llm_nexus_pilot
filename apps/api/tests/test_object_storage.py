"""Unit tests for credential-safe object-storage failure conversion."""

import pytest

from nexuspilot_api.infrastructure import object_storage
from nexuspilot_api.infrastructure.object_storage import ObjectStorage, ObjectStorageError


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
