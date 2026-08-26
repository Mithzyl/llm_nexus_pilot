"""Run-produced object-storage Artifact response schemas."""

from datetime import datetime

from nexuspilot_api.schemas.base import ApiModel


class RunArtifactRead(ApiModel):
    """Expose compatibility metadata for an Artifact produced under one Run."""

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


class RunArtifactSummary(ApiModel):
    """Expose searchable Run Artifact metadata without its object-storage URI."""

    artifact_id: str
    run_id: str
    task_id: str | None
    artifact_type: str
    filename: str
    mime_type: str
    content_hash: str
    size_bytes: int
    created_at: datetime


class RunArtifactDetail(RunArtifactSummary):
    """Expose one Run Artifact and controlled-content availability."""

    content_available: bool = True
