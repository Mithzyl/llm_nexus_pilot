"""Artifact response schemas."""

from datetime import datetime

from nexuspilot_api.schemas.base import ApiModel


class ArtifactRead(ApiModel):
    """Expose metadata and object location for an uploaded artifact."""

    artifact_id: str
    run_id: str
    task_id: str | None
    artifact_type: str
    filename: str
    mime_type: str
    content_hash: str
    storage_uri: str
    size_bytes: int
    created_at: datetime
