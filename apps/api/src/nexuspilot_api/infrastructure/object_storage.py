"""MinIO object storage integration for large files and raw provider payloads."""

import hashlib
import io
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import Any

from anyio import to_thread
from minio import Minio

from nexuspilot_api.core.config import Settings, get_settings


@dataclass(frozen=True)
class StoredObject:
    """Describe an object after its bytes have been durably uploaded."""

    uri: str
    content_hash: str
    size_bytes: int


@dataclass(frozen=True)
class ObjectContent:
    """Describe a lazily streamed object after its metadata has been loaded."""

    chunks: Iterable[bytes]
    size_bytes: int
    content_type: str | None


class ObjectStorageError(RuntimeError):
    """Report a safe object-storage failure without exposing endpoint credentials."""


class ObjectStorageNotFoundError(ObjectStorageError):
    """Report that trusted metadata points to an object that no longer exists."""


class ObjectStorageInvalidURIError(ObjectStorageError):
    """Report an object URI outside the configured MinIO bucket."""


class ObjectStorageIntegrityError(ObjectStorageError):
    """Report object metadata that conflicts with the owning database record."""


class ObjectStorage:
    """Store artifact bytes in a configured MinIO bucket using async-safe wrappers."""

    def __init__(self, settings: Settings | None = None) -> None:
        """Create a MinIO client from validated application settings without network access."""

        self.settings = settings or get_settings()
        self.client = Minio(
            self.settings.minio_endpoint,
            access_key=self.settings.minio_access_key,
            secret_key=self.settings.minio_secret_key,
            secure=self.settings.minio_secure,
        )

    async def put_bytes(self, object_name: str, content: bytes, content_type: str) -> StoredObject:
        """Upload bytes and return metadata, or raise a credential-safe storage error."""

        try:
            await to_thread.run_sync(self._ensure_bucket)
            await to_thread.run_sync(
                lambda: self.client.put_object(
                    self.settings.minio_bucket,
                    object_name,
                    io.BytesIO(content),
                    len(content),
                    content_type=content_type,
                )
            )
        except Exception as exc:
            raise ObjectStorageError("Object storage operation failed.") from exc
        digest = hashlib.sha256(content).hexdigest()
        return StoredObject(
            uri=f"minio://{self.settings.minio_bucket}/{object_name}",
            content_hash=digest,
            size_bytes=len(content),
        )

    async def open_object(
        self,
        storage_uri: str,
        *,
        expected_size_bytes: int | None = None,
    ) -> ObjectContent:
        """Validate object metadata, then open a configured-bucket object for streaming."""

        object_name = self._object_name_from_uri(storage_uri)
        try:
            metadata = await to_thread.run_sync(
                lambda: self.client.stat_object(self.settings.minio_bucket, object_name)
            )
        except Exception as exc:
            if getattr(exc, "code", None) in {"NoSuchKey", "NoSuchObject", "NoSuchBucket"}:
                raise ObjectStorageNotFoundError("Stored object not found.") from exc
            raise ObjectStorageError("Object storage operation failed.") from exc
        if expected_size_bytes is not None and int(metadata.size) != expected_size_bytes:
            raise ObjectStorageIntegrityError("Stored object size does not match metadata.")
        try:
            response = await to_thread.run_sync(
                lambda: self.client.get_object(self.settings.minio_bucket, object_name)
            )
        except Exception as exc:
            if getattr(exc, "code", None) in {"NoSuchKey", "NoSuchObject", "NoSuchBucket"}:
                raise ObjectStorageNotFoundError("Stored object not found.") from exc
            raise ObjectStorageError("Object storage operation failed.") from exc
        return ObjectContent(
            chunks=self._iter_response_chunks(response),
            size_bytes=int(metadata.size),
            content_type=metadata.content_type,
        )

    def _object_name_from_uri(self, storage_uri: str) -> str:
        """Resolve only URIs inside the configured bucket and reject path traversal."""

        prefix = f"minio://{self.settings.minio_bucket}/"
        if not storage_uri.startswith(prefix):
            raise ObjectStorageInvalidURIError("Stored object URI is not allowed.")
        object_name = storage_uri[len(prefix) :]
        path_segments = object_name.split("/")
        if not object_name or any(segment in {"", ".", ".."} for segment in path_segments):
            raise ObjectStorageInvalidURIError("Stored object URI is not allowed.")
        return object_name

    @staticmethod
    def _iter_response_chunks(response: Any) -> Iterator[bytes]:
        """Yield response bytes and always release the underlying HTTP connection."""

        try:
            for chunk in response.stream(amt=64 * 1024):
                if chunk:
                    yield chunk
        finally:
            response.close()
            response.release_conn()

    def _ensure_bucket(self) -> None:
        """Create the configured bucket exactly when the MinIO server reports it missing."""

        if not self.client.bucket_exists(self.settings.minio_bucket):
            self.client.make_bucket(self.settings.minio_bucket)


def get_object_storage() -> ObjectStorage:
    """Construct the request dependency used by artifact upload endpoints."""

    return ObjectStorage()
