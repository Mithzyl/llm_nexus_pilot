"""MinIO object storage integration for large files and raw provider payloads."""

import hashlib
import io
from dataclasses import dataclass

from anyio import to_thread
from minio import Minio

from nexuspilot_api.core.config import Settings, get_settings


@dataclass(frozen=True)
class StoredObject:
    """Describe an object after its bytes have been durably uploaded."""

    uri: str
    content_hash: str
    size_bytes: int


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
        """Create the bucket when needed, upload bytes, and return verifiable object metadata."""

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
        digest = hashlib.sha256(content).hexdigest()
        return StoredObject(
            uri=f"minio://{self.settings.minio_bucket}/{object_name}",
            content_hash=digest,
            size_bytes=len(content),
        )

    def _ensure_bucket(self) -> None:
        """Create the configured bucket exactly when the MinIO server reports it missing."""

        if not self.client.bucket_exists(self.settings.minio_bucket):
            self.client.make_bucket(self.settings.minio_bucket)


def get_object_storage() -> ObjectStorage:
    """Construct the request dependency used by artifact upload endpoints."""

    return ObjectStorage()
